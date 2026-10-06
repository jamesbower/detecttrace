import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NETWORK_ERROR_TEXT } from "../ui-api";
import { UploadCard } from "./UploadCard";

const HOSTILE = "<img src=x onerror=alert(1)>";

function respondWith(status: number, body: unknown) {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status })),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderCard(onUploaded = () => {}) {
  render(
    <UploadCard kind="verdicts" title="Verdicts" acceptText=".csv" accept=".csv,text/csv" onUploaded={onUploaded} />,
  );
  return screen.getByLabelText("Choose files for Verdicts");
}

function csv(name: string): File {
  return new File(["case_id,verdict\n"], name, { type: "text/csv" });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the file input", () => {
  it("is a file input named by its visible label", () => {
    const input = renderCard();

    expect(input.getAttribute("type")).toBe("file");
  });

  it("takes several files", () => {
    const input = renderCard();

    expect(input.hasAttribute("multiple")).toBe(true);
  });

  it("limits the chooser to the accepted suffixes", () => {
    const input = renderCard();

    expect(input.getAttribute("accept")).toBe(".csv,text/csv");
  });

  it("is reached with the Tab key", async () => {
    const input = renderCard();

    await userEvent.tab();

    expect(document.activeElement).toBe(input);
  });
});

describe("uploading", () => {
  it("sends two files as two requests, in order", async () => {
    const fetchMock = respondWith(200, { stored_text: "Done.", problems: [] });
    const input = renderCard();

    await userEvent.upload(input, [csv("a.csv"), csv("b.csv")]);
    await screen.findByText("b.csv");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/upload/verdicts?name=a.csv",
      "/api/upload/verdicts?name=b.csv",
    ]);
  });

  it("sends each dropped file too", async () => {
    const fetchMock = respondWith(200, { stored_text: "Done.", problems: [] });
    renderCard();

    fireEvent.drop(screen.getByText("or drop files here"), { dataTransfer: { files: [csv("dropped.csv")] } });
    await screen.findByText("dropped.csv");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/upload/verdicts?name=dropped.csv"]);
  });

  it("tells the page after each file", async () => {
    respondWith(200, { stored_text: "Done.", problems: [] });
    const onUploaded = vi.fn();
    const input = renderCard(onUploaded);

    await userEvent.upload(input, [csv("a.csv"), csv("b.csv")]);
    await screen.findByText("b.csv");

    expect(onUploaded).toHaveBeenCalledTimes(2);
  });

  it("disables the input while a file is on its way", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const input = renderCard();

    await userEvent.upload(input, [csv("a.csv")]);

    expect(input).toHaveProperty("disabled", true);
  });

  it("announces the file on its way", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const input = renderCard();

    await userEvent.upload(input, [csv("a.csv")]);

    expect(screen.getByRole("status").textContent).toBe("Uploading a.csv…");
  });

  it("announces each file's stored text", async () => {
    respondWith(200, { stored_text: "201 verdicts added.", problems: [] });
    const input = renderCard();

    await userEvent.upload(input, [csv("x.csv")]);

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("x.csv: 201 verdicts added."));
  });
});

describe("a file's result", () => {
  it("titles the server's problems one level below the card's own heading", async () => {
    respondWith(200, {
      stored_text: "201 verdicts added.",
      problems: [
        {
          severity: "invalid_input",
          severity_label: "Invalid input",
          count_text: "3",
          message: "verdict rows have an unknown label.",
          hint: "",
          examples: [],
          more_text: null,
        },
      ],
    });
    const input = renderCard();

    await userEvent.upload(input, [csv("x.csv")]);

    expect((await screen.findByRole("heading", { level: 4 })).textContent).toBe(
      "3 verdict rows have an unknown label.",
    );
  });

  it("shows a refused file's message", async () => {
    respondWith(422, { code: 422, message: "The file has no header row." });
    const input = renderCard();

    await userEvent.upload(input, [csv("x.csv")]);

    expect(await screen.findByText("The file has no header row.")).not.toBeNull();
  });

  it("shows a plain sentence when the app cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    const input = renderCard();

    await userEvent.upload(input, [csv("x.csv")]);

    expect(await screen.findByText(NETWORK_ERROR_TEXT)).not.toBeNull();
  });

  it("shows hostile stored text as text", async () => {
    respondWith(200, { stored_text: HOSTILE, problems: [] });
    const input = renderCard();

    await userEvent.upload(input, [csv("x.csv")]);

    expect((await screen.findByText(HOSTILE)).tagName).toBe("P");
  });

  it("adds no element for hostile stored text", async () => {
    respondWith(200, { stored_text: HOSTILE, problems: [] });
    const input = renderCard();

    await userEvent.upload(input, [csv("x.csv")]);
    await screen.findByText(HOSTILE);

    expect(document.querySelector("img")).toBeNull();
  });
});
