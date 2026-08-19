(function () {
  function csrfHeaders() {
    const token = document.querySelector("meta[name='weekend-report-csrf-token']")?.content;
    return token ? { "X-CSRF-Token": token } : {};
  }

  function toastRegion() {
    let region = document.querySelector("[data-toast-region]");
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.dataset.toastRegion = "";
      region.setAttribute("aria-live", "polite");
      region.setAttribute("aria-atomic", "true");
      document.body.appendChild(region);
    }
    return region;
  }

  function notify(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    toast.textContent = message;
    toastRegion().appendChild(toast);
    window.setTimeout(() => {
      toast.remove();
    }, 7000);
  }

  function setStatus(text, type = "info") {
    document.querySelectorAll("[data-review-status]").forEach((target) => {
      target.textContent = text;
      target.classList.toggle("status-ok", type === "success");
      target.classList.toggle("status-error", type === "error");
    });
  }

  async function saveNote(textarea) {
    const endpoint = textarea.dataset.noteEndpoint;
    if (!endpoint) {
      return null;
    }
    const response = await fetch(endpoint, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...csrfHeaders(),
      },
      body: JSON.stringify({ note: textarea.value }),
    });
    if (!response.ok) {
      const body = await readError(response);
      throw new Error(`${endpoint}: ${response.status} ${body}`);
    }
    return response.json();
  }

  async function readError(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        const body = await response.json();
        if (Array.isArray(body.detail)) {
          return body.detail.join("\n");
        }
        if (body.detail) {
          return String(body.detail);
        }
        return JSON.stringify(body);
      } catch (error) {
        return response.statusText || "Request failed";
      }
    }
    return response.text();
  }

  async function saveAllNotes() {
    const notes = Array.from(document.querySelectorAll("[data-note-endpoint]"));
    for (const textarea of notes) {
      await saveNote(textarea);
    }
    return notes.length;
  }

  function selectedDecision() {
    return document.querySelector("input[name='review-decision']:checked");
  }

  function prepareFinalConfirmation(button) {
    const selected = selectedDecision();
    if (!selected) {
      throw new Error("Select APPROVE or REJECT before final confirmation.");
    }
    const panel = document.querySelector("[data-finalize-confirmation]");
    if (button.dataset.confirmationDecision !== selected.value) {
      button.dataset.confirmationDecision = selected.value;
      button.textContent = `Confirm ${selected.value}`;
      if (panel) {
        panel.hidden = false;
        panel.textContent =
          `You selected ${selected.value}. Click Confirm ${selected.value} to freeze the review snapshot and generate the final PDF.`;
      }
      setStatus("Decision selected. Confirm once more to finalize.", "info");
      notify("Decision selected. Confirm once more to finalize.", "info");
      return false;
    }
    return true;
  }

  function resetFinalConfirmation() {
    document.querySelectorAll("[data-action='finalize']").forEach((button) => {
      delete button.dataset.confirmationDecision;
      button.textContent = "Final Confirmation";
    });
    const panel = document.querySelector("[data-finalize-confirmation]");
    if (panel) {
      panel.hidden = true;
    }
  }

  async function finalize(runId) {
    const selected = selectedDecision();
    if (!selected) {
      throw new Error("Select APPROVE or REJECT before final confirmation.");
    }
    await saveAllNotes();
    const response = await fetch(`/api/runs/${runId}/finalize`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...csrfHeaders(),
      },
      body: JSON.stringify({ decision: selected.value }),
    });
    if (!response.ok) {
      const body = await readError(response);
      throw new Error(`finalize failed: ${response.status} ${body}`);
    }
    return response.json();
  }

  async function createRun() {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: csrfHeaders(),
    });
    if (!response.ok) {
      const body = await readError(response);
      throw new Error(`run creation failed: ${response.status} ${body}`);
    }
    return response.json();
  }

  async function resolveRecovery(button) {
    const note = document.querySelector("[data-recovery-note]")?.value || "";
    const response = await fetch(`/api/runs/${button.dataset.runId}/recovery/resolve`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...csrfHeaders(),
      },
      body: JSON.stringify({ note }),
    });
    if (!response.ok) {
      const body = await readError(response);
      throw new Error(`recovery resolution failed: ${response.status} ${body}`);
    }
    return response.json();
  }

  function exposeFinalPdf(url) {
    if (!url) {
      return;
    }
    const slot = document.querySelector("[data-final-pdf-slot]");
    if (!slot) {
      return;
    }
    const link = document.createElement("a");
    link.className = "button button-primary";
    link.href = url;
    link.textContent = "OPEN FINAL PDF";
    slot.replaceChildren(link);
  }

  document.addEventListener("change", (event) => {
    if (event.target.matches("input[name='review-decision']")) {
      resetFinalConfirmation();
    }
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-create-run-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    try {
      setStatus("Creating run...", "info");
      const result = await createRun();
      notify(`Run ${result.run_id} created.`, "success");
      window.location.assign(`/runs/${result.run_id}`);
    } catch (error) {
      setStatus(error.message, "error");
      notify(error.message, "error");
    }
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) {
      return;
    }
    try {
      if (button.dataset.action === "open-dashboards") {
        const dashboards = Array.from(document.querySelectorAll("[data-dashboard-url]"));
        dashboards.forEach((anchor) => {
          window.open(anchor.href, "_blank", "noopener,noreferrer");
        });
        notify(`Opened ${dashboards.length} configured dashboard${dashboards.length === 1 ? "" : "s"}.`, "success");
      } else if (button.dataset.action === "reload-notes") {
        window.location.reload();
      } else if (button.dataset.action === "save-notes") {
        button.disabled = true;
        const count = await saveAllNotes();
        setStatus(`${count} note field${count === 1 ? "" : "s"} saved.`, "success");
        notify("Notes saved.", "success");
      } else if (button.dataset.action === "finalize") {
        if (!prepareFinalConfirmation(button)) {
          return;
        }
        button.disabled = true;
        const result = await finalize(button.dataset.runId);
        exposeFinalPdf(result.final_pdf_url);
        setStatus(`Final confirmation saved: ${result.decision}.`, "success");
        notify(`Final confirmation saved: ${result.decision}.`, "success");
      } else if (button.dataset.action === "resolve-recovery") {
        button.disabled = true;
        const result = await resolveRecovery(button);
        setStatus(`Recovery resolved. Run state is now ${result.state}.`, "success");
        notify("Recovery resolution saved.", "success");
      }
    } catch (error) {
      setStatus(error.message, "error");
      notify(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
})();
