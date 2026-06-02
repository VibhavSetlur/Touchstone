/**
 * Touchstone GitHub App entry point.
 *
 * Wires up Probot, registers webhook handlers, and exposes a minimal HTTP
 * endpoint for health-checks and webhook delivery.
 */

import { Probot, Server } from "probot";
import { handlePullRequest } from "./handlers/pull-request.js";

const APP_ID = process.env.APP_ID;
const PRIVATE_KEY = process.env.PRIVATE_KEY ?? "";
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET;

if (!APP_ID || !PRIVATE_KEY || !WEBHOOK_SECRET) {
  console.error(
    "Missing required env vars: APP_ID, PRIVATE_KEY (or PRIVATE_KEY_PATH), WEBHOOK_SECRET",
  );
  process.exit(1);
}

const probot = new Probot({
  appId: APP_ID,
  privateKey: PRIVATE_KEY,
  secret: WEBHOOK_SECRET,
});

probot.on(["pull_request.opened", "pull_request.synchronize"], handlePullRequest);

const server = new Server({ Probot: () => probot, port: Number(process.env.PORT ?? 3000) });
await server.load((_app) => {});
await server.start();
console.log(`Touchstone GitHub App listening on :${process.env.PORT ?? 3000}`);
