import { ProcessTerminal, TuiMainScreen, type TUI } from "@earendil-works/pi-tui";
import { Dr4aApp } from "./app.js";
import { ResearchApiClient } from "./api-client.js";

export const resolveApiUrl = (args: string[], env = process.env): string => {
  const index = args.indexOf("--api-url");
  if (index >= 0 && (!args[index + 1] || args[index + 1].startsWith("--"))) throw new Error("--api-url requires an HTTP origin");
  return env.DR4A_API_URL ?? (index >= 0 ? args[index + 1] : undefined) ?? "http://127.0.0.1:8000";
};
const tui: TUI = new TuiMainScreen(new ProcessTerminal());
new Dr4aApp(tui, new ResearchApiClient(resolveApiUrl(process.argv.slice(2))));
tui.start();
