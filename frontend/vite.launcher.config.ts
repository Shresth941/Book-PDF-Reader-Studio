import {defineConfig} from "vite";

// Separate entry: no document workspace, API client, user data or runtime models.
export default defineConfig({root: "launcher", publicDir: false,
  build: {outDir: "../launcher-dist", emptyOutDir: true}});
