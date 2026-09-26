import { defineConfig } from "vite";

// Tauri serves this build output; see src-tauri/tauri.conf.json.
export default defineConfig({
  root: ".",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "safari15", // matches the WebKit shipped with macOS 12+
  },
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
  },
});
