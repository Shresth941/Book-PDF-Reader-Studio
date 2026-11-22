import React from "react";
import {createRoot} from "react-dom/client";
import {PublicLauncher} from "../src/PublicLauncher";
import "../src/launcher.css";

createRoot(document.getElementById("root")!).render(<PublicLauncher/>);
