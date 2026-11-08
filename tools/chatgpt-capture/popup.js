import { captureCurrentConversation } from "./capture.mjs";

const button = document.getElementById("capture");
const status = document.getElementById("status");

function show(message, kind = "info") {
  status.textContent = message;
  status.dataset.kind = kind;
}

button.addEventListener("click", async () => {
  button.disabled = true;
  show("Reading saved graph and auditing the server's current branch…");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id || !/^https:\/\/chatgpt\.com\/(?:c|g|p)\//.test(tab.url || "")) {
      throw new Error("Open a conversation at chatgpt.com/c/… first.");
    }
    const executions = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      world: "ISOLATED",
      func: captureCurrentConversation
    });
    const result = executions?.[0]?.result;
    if (!result || !result.ok) throw new Error(result?.error || "Capture did not finish.");
    const details = result.nodes + " native nodes, " + result.branchPoints + " fork points.";
    if (result.audit === "audited" &&
        result.missingFromGraph === 0 && result.uncomparableMessages === 0) {
      show("Archive requested: " + result.filename + "\n" + details +
           "\nThe selected-branch audit found no extra IDs. This does not prove all branches are present.");
    } else if (result.audit === "audited") {
      show("Archive requested: " + result.filename + "\n" + details +
           "\nWarning: " + result.missingFromGraph + " selected-branch messages absent from native graph; " +
           result.uncomparableMessages + " IDs could not be compared. See capture-manifest.json.", "warning");
    } else {
      show("Archive requested: " + result.filename + "\n" + details +
           "\nWarning: selected-branch audit unavailable. See capture-manifest.json.", "warning");
    }
  } catch (error) {
    show(error instanceof Error ? error.message : "Capture failed.", "error");
  } finally {
    button.disabled = false;
  }
});
