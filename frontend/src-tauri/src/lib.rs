use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, State, WindowEvent};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

const BACKEND_HEALTH_URL: &str = "http://127.0.0.1:8000/healthz";
const BACKEND_STATUS_URL: &str = "http://127.0.0.1:8000/system/status";
const READY_TIMEOUT_SECS: u64 = 20;

/// Passed by the autostart plugin so a boot launch doesn't put a window in front of
/// someone who was doing something else. Also honoured on a manual launch, which is what
/// makes it testable without rebooting.
const HIDDEN_ARG: &str = "--hidden";

/// How often the supervisor asks the backend whether it is still there, and how long it
/// waits after a failed restart before trying again. The backoff exists so a backend that
/// cannot start — a corrupt database, a port taken by something that isn't ours — is not
/// respawned every thirty seconds for as long as the app is open.
const SUPERVISOR_POLL_SECS: u64 = 30;
const SUPERVISOR_MIN_BACKOFF_SECS: u64 = 30;
const SUPERVISOR_MAX_BACKOFF_SECS: u64 = 300;

/// Set the moment the user confirms Quit, and never cleared.
///
/// Two things read it. The window-close handler, so that the close which follows `exit()`
/// isn't turned back into a hide — that would leave the app unable to quit at all. And the
/// supervisor, so it doesn't treat the backend we just deliberately killed as a crash and
/// start it up again while the app is shutting down.
static QUITTING: AtomicBool = AtomicBool::new(false);

/// Holds the sidecar's PID so the shutdown hook can kill it. `None` when nothing was
/// spawned (e.g. a dev backend was already running on the port).
///
/// Deliberately just the PID, not the `CommandChild` handle — PyInstaller's onefile
/// bootloader spawns a second child process on Windows (bootloader -> actual Python
/// process) to do the real work, so killing only the direct child via
/// `CommandChild::kill()` leaves that grandchild running and still holding the port.
/// `taskkill /T` (kill the whole process tree) is the reliable fix.
struct SidecarState(Mutex<Option<u32>>);

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

/// Restart the whole app so a staged database restore can be applied.
///
/// The restore itself happens in the backend's bootstrap, before it opens the database — see
/// backend/app/bootstrap.py's maybe_apply_staged_restore. All this has to do is make sure the
/// current backend process really dies and a fresh one starts.
///
/// `request_restart()`, deliberately, NOT `restart()`. Tauri documents that `restart()` called on
/// the main thread cannot guarantee delivery of exit events and skips them — and a synchronous
/// `#[tauri::command]` runs on the main thread. Skipping them means `RunEvent::Exit` never fires,
/// so the `taskkill` in the run handler below never runs, the old sidecar survives still holding
/// the database file, and `spawn_sidecar_if_needed` in the new process adopts it. The restore
/// would then silently never apply. `request_restart()` always routes through the ordinary exit
/// path, so the reaper runs first.
///
/// It also emits `ExitRequested` rather than a window `CloseRequested`, which means the
/// frontend's unsaved-changes close guard can't veto it into a deadlock. The frontend guards this
/// call itself instead (see restartApp's caller).
#[tauri::command]
fn restart_app(app: tauri::AppHandle) {
    app.request_restart();
}

async fn is_backend_healthy() -> bool {
    let client = match reqwest::Client::builder().timeout(Duration::from_secs(2)).build() {
        Ok(c) => c,
        Err(_) => return false,
    };
    matches!(client.get(BACKEND_HEALTH_URL).send().await, Ok(resp) if resp.status().is_success())
}

/// The version the backend on the port reports, if it will tell us.
///
/// `None` means it answered but named no version, which identifies a build from before
/// /healthz carried one — i.e. an old release, which is precisely the case worth catching.
async fn backend_reported_version() -> Option<String> {
    let client = reqwest::Client::builder().timeout(Duration::from_secs(2)).build().ok()?;
    let response = client.get(BACKEND_HEALTH_URL).send().await.ok()?;
    let body = response.json::<serde_json::Value>().await.ok()?;
    body.get("version")?.as_str().map(|v| v.to_string())
}

/// Whether the backend on the port is the same build as this shell.
///
/// Windows cannot overwrite a running executable, so an update applied while the old
/// backend is alive replaces the app and silently leaves the previous sidecar binary in
/// place. The new shell then finds a healthy backend on 8000 and adopts it, and the user
/// runs a new UI against an old API — which looks like nothing at all until a page calls an
/// endpoint that release never had. That is exactly how 0.6.2 shipped a working app with a
/// broken Integrations page.
///
/// A backend reporting "dev" is always accepted: that is a developer's own process, started
/// by hand, and refusing it would break the reuse-if-running convenience for no safety gain.
fn version_matches(reported: Option<&str>, own: &str) -> bool {
    match reported {
        Some("dev") => true,
        Some(v) => v == own,
        // Answered, but named no version: a build from before /healthz carried one.
        None => false,
    }
}

/// Whether the backend answering on the port is one we should reuse.
///
/// Healthy is not sufficient. A backend that reports a maintenance `phase` is the *outgoing*
/// process from a restart-for-restore that hasn't finished dying yet. Adopting it would leave the
/// new shell talking to a process still holding the old database, and the staged restore would
/// never be applied — silently, since everything would otherwise look fine.
///
/// Anything other than a clear "I am in maintenance" counts as adoptable, including an older
/// build with no /system/status at all. Being conservative in the other direction would mean
/// refusing to reuse a perfectly good dev backend.
async fn is_backend_adoptable() -> bool {
    let client = match reqwest::Client::builder().timeout(Duration::from_secs(2)).build() {
        Ok(c) => c,
        Err(_) => return false,
    };
    let Ok(response) = client.get(BACKEND_STATUS_URL).send().await else {
        return true;
    };
    let Ok(body) = response.json::<serde_json::Value>().await else {
        return true;
    };
    body.get("phase").map(|p| p.is_null()).unwrap_or(true)
}

async fn wait_for_backend_ready(timeout_secs: u64) -> bool {
    let start = Instant::now();
    while start.elapsed().as_secs() < timeout_secs {
        if is_backend_healthy().await {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    false
}

/// A file under `%LOCALAPPDATA%\StockSmith\` — the same directory the backend resolves for
/// itself in `app/bootstrap.py`. Everything the shell writes lives beside the backend's own
/// files rather than in a second location, so "delete this folder to reset the app" (README)
/// stays true.
fn stocksmith_data_path(file_name: &str) -> Option<std::path::PathBuf> {
    std::env::var_os("LOCALAPPDATA").map(|dir| std::path::Path::new(&dir).join("StockSmith").join(file_name))
}

fn backend_log_path() -> Option<std::path::PathBuf> {
    stocksmith_data_path("backend.log")
}

/// Where the spawned sidecar's PID is recorded.
///
/// The port alone cannot answer "is there an orphan of ours running": a sidecar that is
/// alive but wedged — mid-crash, or holding the database without listening — answers
/// nothing on 8000, so the health probe reads it as absent and the shell spawns a second
/// one beside it. Two backends on one SQLite file is the failure this file prevents, and it
/// is the half of the 0.6.3 adoption fix that didn't ship with it
/// (`docs/plan-background-sync.md` §2b).
fn backend_pid_path() -> Option<std::path::PathBuf> {
    stocksmith_data_path("backend.pid")
}

/// Written when the user quits deliberately, cleared on every launch.
///
/// Nothing in Tier 1 reads it — the watchdog task in `docs/plan-background-sync.md` §6c
/// does, and this is written now because the alternative is a watchdog that resurrects an
/// app somebody closed on purpose. Cheap to write now, awkward to retrofit later.
fn stay_down_marker_path() -> Option<std::path::PathBuf> {
    stocksmith_data_path("backend.stopped")
}

/// Marks that the "still running in the tray" notice has been shown on this install.
fn tray_notice_marker_path() -> Option<std::path::PathBuf> {
    stocksmith_data_path("tray-notice-shown")
}

/// The PID in a pid-file's contents, if it holds one.
///
/// Separate from the file read so the parsing is testable, and deliberately strict: an
/// empty or truncated file (a crash mid-write) yields None rather than a PID that would
/// then be handed to `taskkill`.
fn parse_pid(contents: &str) -> Option<u32> {
    contents.trim().parse::<u32>().ok().filter(|pid| *pid != 0)
}

fn read_recorded_pid() -> Option<u32> {
    let path = backend_pid_path()?;
    parse_pid(&std::fs::read_to_string(path).ok()?)
}

fn write_recorded_pid(pid: u32) {
    let Some(path) = backend_pid_path() else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, pid.to_string());
}

fn clear_recorded_pid() {
    if let Some(path) = backend_pid_path() {
        let _ = std::fs::remove_file(path);
    }
}

/// Whether this PID is a live StockSmith backend — not merely a live process.
///
/// The image-name filter is the point. PIDs are recycled, and a stale pid-file naming a
/// number Windows has since handed to something else would otherwise make the reaper kill
/// an unrelated program. Asking for both means a mismatch simply reports "not running".
fn backend_process_is_alive(pid: u32) -> bool {
    let output = std::process::Command::new("tasklist")
        .args([
            "/FI",
            &format!("PID eq {pid}"),
            "/FI",
            "IMAGENAME eq stocksmith-backend.exe",
            "/NH",
        ])
        .output();
    match output {
        Ok(out) => String::from_utf8_lossy(&out.stdout).contains("stocksmith-backend"),
        Err(_) => false,
    }
}

/// `/T` kills the whole process tree — see the `SidecarState` doc comment for why the
/// direct child alone isn't enough on Windows.
fn kill_process_tree(pid: u32) {
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .output();
}

/// Kills a sidecar left behind by a previous run, before spawning a new one.
///
/// Only ever called once the port has been found silent, so a healthy backend — ours or a
/// developer's — is never what this reaps. The ownership rule this implements is in
/// `docs/plan-background-sync.md` §6d: nothing kills a healthy backend of the right
/// version.
fn reap_orphaned_sidecar() {
    let Some(pid) = read_recorded_pid() else { return };
    if backend_process_is_alive(pid) {
        append_backend_log(&format!(
            "[shell] Reaping orphaned backend process {pid} recorded in backend.pid (nothing answering on 8000)"
        ));
        kill_process_tree(pid);
    }
    clear_recorded_pid();
}

fn append_backend_log(line: &str) {
    let Some(path) = backend_log_path() else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{}", line.trim_end());
    }
}

fn show_startup_error(app: &tauri::AppHandle, message: &str) {
    app.dialog()
        .message(message)
        .title("StockSmith — Backend Error")
        .kind(tauri_plugin_dialog::MessageDialogKind::Error)
        .blocking_show();
}

async fn spawn_sidecar_if_needed(app: &tauri::AppHandle) -> Result<(), String> {
    if is_backend_healthy().await && is_backend_adoptable().await {
        // Something (e.g. a manually-started dev backend) already answers on the port —
        // reuse it instead of spawning a second instance, matching the "reuse if already
        // running" convenience the old dev scripts had. Unless it's mid-restore: see
        // is_backend_adoptable.
        //
        // But only if it is the same build. Adopting an older backend is silent and
        // indistinguishable from working, so this refuses rather than guesses — see
        // version_matches.
        let own = app.package_info().version.to_string();
        let reported = backend_reported_version().await;
        if version_matches(reported.as_deref(), &own) {
            // Adopting one of our own orphans means adopting responsibility for it too:
            // record it as ours so Quit reaps it and the supervisor watches it. Without
            // this, a backend that survived a crashed shell would be used happily and then
            // left running forever, which is how the port ends up occupied by a process
            // nobody remembers starting.
            //
            // A backend with no pid-file is a developer's own `uv run uvicorn` — used, but
            // never owned, so quitting the app leaves their session alone.
            if let Some(pid) = read_recorded_pid() {
                if backend_process_is_alive(pid) {
                    let state: State<SidecarState> = app.state();
                    *state.0.lock().unwrap() = Some(pid);
                }
            }
            return Ok(());
        }
        return Err(format!(
            "A different version of the StockSmith backend is already running on port 8000              (it reports {}, this app is {}). This usually means an update could not replace              the backend because it was still running. Close StockSmith completely, then run              the installer again.",
            reported.as_deref().unwrap_or("an older build"),
            own
        ));
    }

    // Nothing is answering on the port. If a previous run recorded a sidecar that is somehow
    // still alive, it is wedged rather than serving — kill it before starting another, or
    // both end up holding the same SQLite file.
    reap_orphaned_sidecar();

    // Hand the shell's own version to the backend rather than keeping a second copy of it in
    // Python. The backend records it in every backup manifest and reports it from
    // /system/status; a restore uses it to explain *which* build wrote a backup it can't read.
    let sidecar = app
        .shell()
        .sidecar("stocksmith-backend")
        .map_err(|e| format!("Failed to locate backend sidecar: {e}"))?
        .env("STOCKSMITH_APP_VERSION", app.package_info().version.to_string());
    let (mut rx, child) = sidecar.spawn().map_err(|e| format!("Failed to start backend: {e}"))?;

    let state: State<SidecarState> = app.state();
    *state.0.lock().unwrap() = Some(child.pid());
    write_recorded_pid(child.pid());

    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) | CommandEvent::Stderr(bytes) => {
                    append_backend_log(&String::from_utf8_lossy(&bytes));
                }
                _ => {}
            }
        }
    });

    Ok(())
}

/// Upper bound on the release notes shown in the update dialog. A native message dialog
/// doesn't scroll, so an over-long body would push the buttons out of reach.
const MAX_NOTES_CHARS: usize = 1200;

/// Trims release notes to `limit` characters, cutting at a line boundary so the result
/// never ends mid-sentence. Counts chars rather than bytes so a multi-byte character
/// can't be split (which would panic on a slice).
fn truncate_notes(notes: &str, limit: usize) -> String {
    if notes.chars().count() <= limit {
        return notes.to_string();
    }
    let clipped: String = notes.chars().take(limit).collect();
    let cut = clipped.rfind('\n').unwrap_or(clipped.len());
    format!("{}\n\n(…see the full release notes on GitHub)", clipped[..cut].trim_end())
}

/// Checks GitHub Releases (via the endpoint configured in tauri.conf.json) for a newer
/// signed release. Silently does nothing if the check fails (e.g. no release published
/// yet, no network) or the user declines — either way, normal startup continues. Never
/// returns if the user accepts and the install succeeds: `app.restart()` diverges.
async fn check_for_update_and_maybe_install(app: &tauri::AppHandle) {
    let updater = match app.updater() {
        Ok(u) => u,
        Err(_) => return,
    };
    let update = match updater.check().await {
        Ok(Some(update)) => update,
        _ => return,
    };

    // Release notes come from the `notes` field in latest.json, which the release
    // workflow fills from the matching CHANGELOG.md section. Older releases (and any
    // build where the section was missing) have none, so the version-only wording stays
    // as the fallback rather than leaving an empty gap in the dialog.
    //
    // Truncated because this is a native OS dialog with no scrollbar — an unbounded body
    // would push the Yes/No buttons off-screen on a long changelog, leaving the user
    // unable to answer it at all.
    let notes = update.body.as_deref().unwrap_or("").trim();
    let message = if notes.is_empty() {
        format!(
            "A new version ({}) is available. Install it now? The app will restart.",
            update.version
        )
    } else {
        format!(
            "A new version ({}) is available.\n\nWhat's new:\n{}\n\nInstall it now? The app will restart.",
            update.version,
            truncate_notes(notes, MAX_NOTES_CHARS)
        )
    };

    let confirmed = app
        .dialog()
        .message(message)
        .title("StockSmith Update Available")
        .buttons(MessageDialogButtons::YesNo)
        .blocking_show();

    if !confirmed {
        return;
    }

    if update.download_and_install(|_, _| {}, || {}).await.is_err() {
        return;
    }

    app.restart();
}

/// The window itself is visible from the moment the app launches — the webview renders its
/// own splash screen (see frontend `SplashScreen.tsx`) while it waits for the backend to
/// answer. This just spawns the backend and, if it never comes up, surfaces an error dialog;
/// it no longer needs to toggle window visibility.
async fn start_backend(app: tauri::AppHandle) {
    check_for_update_and_maybe_install(&app).await;

    if let Err(message) = spawn_sidecar_if_needed(&app).await {
        show_startup_error(&app, &message);
        return;
    }

    if !wait_for_backend_ready(READY_TIMEOUT_SECS).await {
        show_startup_error(
            &app,
            "The backend did not become ready in time. Check backend.log under %LOCALAPPDATA%\\StockSmith\\.",
        );
    }

    supervise_sidecar(app).await;
}

/// Restarts the backend if it dies while the app is running.
///
/// Until now the sidecar was spawned once and never looked at again, so a backend that
/// crashed at 2am left the app sitting there looking fine and syncing nothing until someone
/// noticed. That matters much more once the window can be closed without quitting: the
/// symptom used to be visible within a day because the app was being restarted anyway.
///
/// Only supervises a backend this shell owns. A developer's own `uv run uvicorn`, adopted
/// but not owned, is left alone — restarting it from here would fight whoever is restarting
/// it on purpose.
async fn supervise_sidecar(app: tauri::AppHandle) {
    let mut backoff_secs = SUPERVISOR_MIN_BACKOFF_SECS;

    loop {
        tokio::time::sleep(Duration::from_secs(SUPERVISOR_POLL_SECS)).await;
        if QUITTING.load(Ordering::SeqCst) {
            return;
        }

        let owned = {
            let state: State<SidecarState> = app.state();
            let guard = state.0.lock().unwrap();
            guard.is_some()
        };
        if !owned {
            continue;
        }

        if is_backend_healthy().await {
            backoff_secs = SUPERVISOR_MIN_BACKOFF_SECS;
            continue;
        }

        // One missed probe is not a crash. The backend holds its event loop through a
        // backup, a restore swap and a long marketplace sync, any of which can outlast a
        // two-second HTTP timeout on a machine doing other work — which this one is.
        tokio::time::sleep(Duration::from_secs(2)).await;
        if is_backend_healthy().await || QUITTING.load(Ordering::SeqCst) {
            continue;
        }

        append_backend_log("[shell] Backend stopped answering — restarting it");
        match spawn_sidecar_if_needed(&app).await {
            Ok(()) if wait_for_backend_ready(READY_TIMEOUT_SECS).await => {
                append_backend_log("[shell] Backend restarted");
                backoff_secs = SUPERVISOR_MIN_BACKOFF_SECS;
                continue;
            }
            Ok(()) => append_backend_log("[shell] Restarted backend did not become ready"),
            Err(message) => append_backend_log(&format!("[shell] Could not restart backend: {message}")),
        }

        // Deliberately silent to the user. A dialog from a background thread at 3am helps
        // nobody, and the sync-coverage reading in Settings is where a repeated failure is
        // meant to show up (docs/plan-background-sync.md §7).
        append_backend_log(&format!("[shell] Next restart attempt in {backoff_secs}s"));
        tokio::time::sleep(Duration::from_secs(backoff_secs)).await;
        backoff_secs = (backoff_secs * 2).min(SUPERVISOR_MAX_BACKOFF_SECS);
    }
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// Shown once per install, the first time closing the window hides it.
///
/// A window that vanishes instead of quitting is the single most surprising part of this
/// feature, and someone who doesn't know where it went will go looking in Task Manager and
/// kill it — which is precisely the outcome the tray exists to prevent.
fn show_tray_notice_once(app: &tauri::AppHandle) {
    let Some(path) = tray_notice_marker_path() else { return };
    if path.exists() {
        return;
    }
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // Written before the dialog, not after: if showing it fails, the right outcome is one
    // missed notice, not a dialog on every close forever.
    let _ = std::fs::write(&path, "shown");

    let app = app.clone();
    std::thread::spawn(move || {
        app.dialog()
            .message(
                "StockSmith is still running in the notification area, so it keeps syncing with Etsy and eBay while the window is closed.\n\nClick its tray icon to open it again, or use Quit there to close it properly.",
            )
            .title("StockSmith is still running")
            .blocking_show();
    });
}

/// Quit — the only path that actually exits, now that closing the window hides it.
///
/// Confirmed, for two reasons that point the same way: quitting is what stops stock syncing
/// (the whole point of the tray), and it is now reachable from a menu that sits one slip
/// away from "Open". The dialog is also where unsaved work gets its warning, since hiding
/// keeps the window alive and only a real quit can lose anything.
fn confirm_and_quit(app: &tauri::AppHandle) {
    let app = app.clone();
    // Off the main thread: a tray menu handler runs on the event loop, and a modal dialog
    // shown from there blocks the very loop that has to draw it.
    std::thread::spawn(move || {
        let confirmed = app
            .dialog()
            .message(
                "StockSmith will stop syncing with Etsy and eBay until you open it again.\n\nAnything unsaved in an open form will be lost.",
            )
            .title("Quit StockSmith?")
            .buttons(MessageDialogButtons::YesNo)
            .blocking_show();
        if !confirmed {
            return;
        }

        QUITTING.store(true, Ordering::SeqCst);
        if let Some(path) = stay_down_marker_path() {
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(path, "quit");
        }
        app.exit(0);
    });
}

fn build_tray(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let open = MenuItem::with_id(app, "open", "Open StockSmith", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit StockSmith", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &separator, &quit])?;

    let mut builder = TrayIconBuilder::with_id("main")
        .tooltip("StockSmith — syncing in the background")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main_window(app),
            "quit" => confirm_and_quit(app),
            _ => {}
        });
    // The window icon doubles as the tray icon rather than shipping a second asset. If a
    // build somehow has no default icon, a tray entry with no picture is still better than
    // no tray entry at all — losing it would leave a hidden window with no way back.
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
    }
    builder.build(app)?;
    Ok(())
}

/// Whether Windows is currently set to start StockSmith at sign-in.
///
/// Read live from the registry entry every time rather than cached anywhere, because there
/// are two ways it can change behind the app's back: Windows' own Startup Apps settings,
/// and a standing report that the plugin's own entry disappears after the first boot
/// (plugins-workspace#771). A toggle that remembers what it was told would go on claiming
/// autostart is on in both cases.
#[tauri::command]
fn autostart_enabled(app: tauri::AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

/// Returns the state read back afterwards, not the state asked for — so a write that
/// silently didn't take surfaces in the UI rather than looking like success.
#[tauri::command]
fn set_autostart(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    let manager = app.autolaunch();
    if enabled {
        manager.enable().map_err(|e| e.to_string())?;
    } else {
        manager.disable().map_err(|e| e.to_string())?;
    }
    Ok(manager.is_enabled().unwrap_or(false))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Registered before every other plugin, per the plugin's own guidance: a second
        // launch has to be intercepted before the rest of the app starts setting itself up,
        // or two shells briefly race to spawn or adopt the same sidecar.
        //
        // Autostart makes this mandatory rather than merely tidy — a boot launch plus a
        // Start-menu launch is two instances, and with the window hidden the second one
        // looks to the user like the app simply failing to open.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app);
        }))
        // `--hidden` so a boot launch doesn't throw a window in front of whoever is using
        // the machine for something else. The flag is honoured on any launch, which is what
        // makes it testable without rebooting.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![HIDDEN_ARG]),
        ))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_upload::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(SidecarState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![greet, restart_app, autostart_enabled, set_autostart])
        .setup(|app| {
            // Cleared on every launch: the marker means "the user quit deliberately, stay
            // down", and starting the app is the user saying otherwise.
            if let Some(path) = stay_down_marker_path() {
                let _ = std::fs::remove_file(path);
            }

            build_tray(app.handle())?;

            if std::env::args().any(|arg| arg == HIDDEN_ARG) {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                start_backend(handle).await;
            });
            Ok(())
        })
        // Closing the window hides it; only the tray's Quit exits. This lives in Rust rather
        // than the frontend so it still works when the webview is wedged — an X that does
        // nothing is the failure people reach for Task Manager over, and killing the shell
        // that way is exactly what orphans a sidecar (§2b).
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if QUITTING.load(Ordering::SeqCst) {
                    return;
                }
                api.prevent_close();
                let _ = window.hide();
                show_tray_notice_once(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        // Reap the sidecar on Exit, NOT on CloseRequested.
        //
        // CloseRequested fires when the user *asks* to close, which the frontend can now
        // veto: the window has unsaved work, so it calls preventDefault() and shows a
        // confirmation (see useTauriCloseGuard). Killing the backend there meant a user who
        // chose "Keep editing" was left in a live window with a dead backend and no way to
        // save the very work they'd just protected. Exit only fires once the app is really
        // going away.
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                QUITTING.store(true, Ordering::SeqCst);
                let state: State<SidecarState> = app_handle.state();
                let mut guard = state.0.lock().unwrap();
                if let Some(pid) = guard.take() {
                    kill_process_tree(pid);
                    // Only after the kill: a pid-file outliving its process is harmless (the
                    // reaper checks liveness), but a process outliving its pid-file is an
                    // orphan nothing will ever clean up.
                    clear_recorded_pid();
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::version_matches;

    #[test]
    fn adopts_a_backend_of_the_same_version() {
        assert!(version_matches(Some("0.6.3"), "0.6.3"));
    }

    #[test]
    fn refuses_a_backend_from_a_different_release() {
        // The 0.6.2 failure: a new shell finding the previous release's backend still alive
        // because the installer could not overwrite a running executable.
        assert!(!version_matches(Some("0.6.1"), "0.6.2"));
    }

    #[test]
    fn refuses_a_backend_that_reports_no_version() {
        // Answering without a version identifies a build from before /healthz carried one,
        // which is by definition older than any shell performing this check.
        assert!(!version_matches(None, "0.6.3"));
    }

    #[test]
    fn always_adopts_a_developers_own_backend() {
        // Started by hand from source; refusing it would break reuse-if-running for no gain.
        assert!(version_matches(Some("dev"), "0.6.3"));
    }

    use super::parse_pid;

    #[test]
    fn reads_a_pid_from_a_pid_file() {
        assert_eq!(parse_pid("12345"), Some(12345));
        // Written with a trailing newline by anything that appends one.
        assert_eq!(parse_pid("12345\r\n"), Some(12345));
    }

    #[test]
    fn refuses_a_truncated_or_empty_pid_file() {
        // A crash mid-write leaves a partial file. Returning None means "nothing to reap",
        // where a lenient parse would hand a stray number to taskkill.
        assert_eq!(parse_pid(""), None);
        assert_eq!(parse_pid("   "), None);
        assert_eq!(parse_pid("not-a-pid"), None);
        assert_eq!(parse_pid("123abc"), None);
    }

    #[test]
    fn refuses_pid_zero() {
        // PID 0 is the system idle process on Windows and never ours; killing a tree rooted
        // there is not something to attempt on the strength of a stale file.
        assert_eq!(parse_pid("0"), None);
    }

    use super::{truncate_notes, MAX_NOTES_CHARS};

    #[test]
    fn short_notes_pass_through_unchanged() {
        let notes = "### Fixed\n- Something small.";
        assert_eq!(truncate_notes(notes, MAX_NOTES_CHARS), notes);
    }

    #[test]
    fn long_notes_are_cut_at_a_line_boundary() {
        let notes = "line one\nline two\nline three";
        let out = truncate_notes(notes, 14); // lands inside "line two"
        assert!(out.starts_with("line one"));
        assert!(!out.contains("line two"));
        assert!(out.contains("see the full release notes"));
    }

    #[test]
    fn multibyte_characters_are_not_split() {
        // Slicing by byte index here would panic — the em dashes are 3 bytes each.
        let notes = "— — — — — — — — — —\nsecond line";
        let out = truncate_notes(notes, 5);
        assert!(out.contains("see the full release notes"));
    }

    #[test]
    fn a_single_overlong_line_still_truncates() {
        // No newline to cut at — must not panic or return the whole thing.
        let notes = "a".repeat(100);
        let out = truncate_notes(&notes, 10);
        assert!(out.len() < notes.len());
        assert!(out.contains("see the full release notes"));
    }
}
