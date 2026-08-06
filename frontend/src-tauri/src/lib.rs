use std::io::Write;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, State};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

const BACKEND_HEALTH_URL: &str = "http://127.0.0.1:8000/healthz";
const READY_TIMEOUT_SECS: u64 = 20;

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

async fn is_backend_healthy() -> bool {
    let client = match reqwest::Client::builder().timeout(Duration::from_secs(2)).build() {
        Ok(c) => c,
        Err(_) => return false,
    };
    matches!(client.get(BACKEND_HEALTH_URL).send().await, Ok(resp) if resp.status().is_success())
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

fn backend_log_path() -> Option<std::path::PathBuf> {
    std::env::var_os("LOCALAPPDATA").map(|dir| std::path::Path::new(&dir).join("StockSmith").join("backend.log"))
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
    if is_backend_healthy().await {
        // Something (e.g. a manually-started dev backend) already answers on the port —
        // reuse it instead of spawning a second instance, matching the "reuse if already
        // running" convenience the old dev scripts had.
        return Ok(());
    }

    let sidecar = app
        .shell()
        .sidecar("stocksmith-backend")
        .map_err(|e| format!("Failed to locate backend sidecar: {e}"))?;
    let (mut rx, child) = sidecar.spawn().map_err(|e| format!("Failed to start backend: {e}"))?;

    let state: State<SidecarState> = app.state();
    *state.0.lock().unwrap() = Some(child.pid());

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
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_upload::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(SidecarState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![greet])
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                start_backend(handle).await;
            });
            Ok(())
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
                let state: State<SidecarState> = app_handle.state();
                let mut guard = state.0.lock().unwrap();
                if let Some(pid) = guard.take() {
                    // /T kills the whole process tree — see the SidecarState doc comment
                    // for why the direct child alone isn't enough on Windows.
                    let _ = std::process::Command::new("taskkill")
                        .args(["/F", "/T", "/PID", &pid.to_string()])
                        .output();
                }
            }
        });
}

#[cfg(test)]
mod tests {
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
