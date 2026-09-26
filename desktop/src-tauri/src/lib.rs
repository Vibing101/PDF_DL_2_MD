//! The desktop shell.
//!
//! All of the app's logic lives in the web view (`src/`) and in the Python
//! sidecar it speaks to, so this is only here to register the plugins the front
//! end uses: the file/folder pickers, spawning the sidecar, and revealing the
//! finished folder in Finder.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running the pdf2md desktop app");
}
