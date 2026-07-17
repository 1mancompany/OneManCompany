#!/usr/bin/env node
/* Regression test for the frontend's conversation_message router.
 *
 * Bug: PRODUCT planning conversations are opened in the CEO terminal
 * (frontend/app.js:_openProductPlanningConversation sets
 *  _currentConvType = 'product'), but the conversation_message handler's
 * terminal-routing branch only matched _currentConvType === 'oneonone' ||
 * 'ea_chat'. EA replies for product planning thus arrived via WebSocket
 * but were never rendered — they only showed up in server logs because
 * debug logging captured the LLM output text.
 *
 * Two assertions:
 *  1. Source-level: the routing filter in app.js includes 'product'.
 *  2. Behavioral: a small reimplementation of the routing predicate
 *     returns true for {convType: 'product', conv_id matches}.
 */

const fs = require("fs");
const path = require("path");

const appJsPath = path.resolve(__dirname, "..", "..", "frontend", "app.js");
const appJsSrc = fs.readFileSync(appJsPath, "utf-8");

let failures = 0;
function assert(cond, msg) {
  if (cond) console.log(`  ok  ${msg}`);
  else {
    failures += 1;
    console.log(`  FAIL  ${msg}`);
  }
}

// ── Source-level invariant ────────────────────────────────────────────────
// The terminal-routing branch of the conversation_message handler must
// include 'product' alongside 'oneonone' and 'ea_chat'. We grep the
// production source to ensure the fix isn't accidentally reverted.
const filterPattern =
  /this\._currentConvType\s*===\s*'oneonone'\s*\|\|\s*this\._currentConvType\s*===\s*'ea_chat'\s*\|\|\s*this\._currentConvType\s*===\s*'product'/;
assert(
  filterPattern.test(appJsSrc),
  "conversation_message router includes 'product' in the terminal-routing filter",
);

// ── Behavioral mirror ─────────────────────────────────────────────────────
// Re-implement the predicate so a unit test would have caught the bug
// before it shipped. If the production filter ever drifts, the
// source-level assertion above will fail; if our predicate drifts, the
// behavioral cases below will fail.
function shouldRouteToTerminal(currentConvId, currentConvType, messageConvId) {
  if (currentConvId !== messageConvId) return false;
  return (
    currentConvType === "oneonone" ||
    currentConvType === "ea_chat" ||
    currentConvType === "product"
  );
}

assert(
  shouldRouteToTerminal("c-1", "product", "c-1") === true,
  "product planning replies route to the terminal when the conv is open",
);
assert(
  shouldRouteToTerminal("c-1", "oneonone", "c-1") === true,
  "1-on-1 replies still route to the terminal",
);
assert(
  shouldRouteToTerminal("c-1", "ea_chat", "c-1") === true,
  "EA chat replies still route to the terminal",
);
assert(
  shouldRouteToTerminal("c-1", "product", "c-2") === false,
  "messages for a different conv_id do NOT route to the terminal",
);
assert(
  shouldRouteToTerminal("c-1", "ceo_inbox", "c-1") === false,
  "ceo_inbox conversations do NOT route to terminal (they use chatPanel)",
);

// ── Send-path invariant ───────────────────────────────────────────────────
// Rendering the EA's reply is only half the flow: the CEO must be able to
// reply back. The send handler routes to the conversation API only for
// 'oneonone' / 'product'; without 'product' a reply falls through to the
// task-creation fallback and silently spawns a spurious task.
const sendFilterPattern =
  /this\._currentConvType\s*===\s*'oneonone'\s*\|\|\s*this\._currentConvType\s*===\s*'product'/;
assert(
  sendFilterPattern.test(appJsSrc),
  "send handler routes 'product' replies through the conversation API",
);

// Behavioral mirror of the send predicate.
function shouldSendViaConversationApi(currentConvType, currentConvId) {
  return (
    (currentConvType === "oneonone" || currentConvType === "product") &&
    Boolean(currentConvId)
  );
}
assert(
  shouldSendViaConversationApi("product", "c-1") === true,
  "product planning replies are sent via the conversation API",
);
assert(
  shouldSendViaConversationApi("oneonone", "c-1") === true,
  "1-on-1 replies are still sent via the conversation API",
);
assert(
  shouldSendViaConversationApi("product", null) === false,
  "no send when there is no active conversation id",
);

if (failures) {
  console.log(`\n${failures} failed`);
  process.exit(1);
}
console.log("\nall tests passed");
