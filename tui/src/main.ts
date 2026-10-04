import { ProcessTerminal, TuiMainScreen, type TUI } from "@earendil-works/pi-tui";
import { Dr4aApp } from "./app.js";
import { ResearchApiClient } from "./api-client.js";

export const resolveApiUrl = (args: string[], env = process.env): string => env.DR4A_API_URL ?? args[args.indexOf("--api-url") + 1] ?? "http://127.0.0.1:8000";
const tui: TUI = new TuiMainScreen(new ProcessTerminal());
new Dr4aApp(tui, new ResearchApiClient(resolveApiUrl(process.argv.slice(2))));
tui.start();
