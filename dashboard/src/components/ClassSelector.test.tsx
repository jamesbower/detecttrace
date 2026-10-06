import { cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { createClasses } from "../test-fixtures";
import { ClassPanel, ClassSelector, MAX_CLASS_TABS, getClassTabId } from "./ClassSelector";

const THREE = createClasses(3);
const MANY = createClasses(MAX_CLASS_TABS + 1);

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "#");
});

function selectedTabName(): string | null {
  return screen.getByRole("tab", { selected: true }).textContent;
}

describe("as tabs", () => {
  it("shows a tab list for up to six classes", () => {
    render(<ClassSelector classes={createClasses(MAX_CLASS_TABS)} panelId="panel" />);

    expect(screen.getAllByRole("tab")).toHaveLength(MAX_CLASS_TABS);
  });

  it("labels the tab list", () => {
    render(<ClassSelector classes={THREE} panelId="panel" />);

    expect(screen.queryByRole("tablist", { name: "Alert class" })).not.toBeNull();
  });

  it("selects the first class by default", () => {
    render(<ClassSelector classes={THREE} panelId="panel" />);

    expect(selectedTabName()).toBe("class_0");
  });

  it("selects the class the hash names", () => {
    window.history.replaceState(null, "", "#/?class=class-2");
    render(<ClassSelector classes={THREE} panelId="panel" />);

    expect(selectedTabName()).toBe("class_2");
  });

  it("selects the first class when the hash names an unknown one", () => {
    window.history.replaceState(null, "", "#/?class=class-9");
    render(<ClassSelector classes={THREE} panelId="panel" />);

    expect(selectedTabName()).toBe("class_0");
  });

  it("points each tab at the page's panel", () => {
    render(<ClassSelector classes={THREE} panelId="versions-panel" />);

    expect(screen.getAllByRole("tab").map((tab) => tab.getAttribute("aria-controls"))).toEqual([
      "versions-panel",
      "versions-panel",
      "versions-panel",
    ]);
  });

  it("lets only the selected tab take focus from Tab", () => {
    window.history.replaceState(null, "", "#/?class=class-1");
    render(<ClassSelector classes={THREE} panelId="panel" />);

    expect(screen.getAllByRole("tab").map((tab) => tab.tabIndex)).toEqual([-1, 0, -1]);
  });

  it("stores a clicked class's anchor in the hash", async () => {
    render(<ClassSelector classes={THREE} panelId="panel" />);

    await userEvent.click(screen.getByRole("tab", { name: "class_1" }));

    expect(window.location.hash).toBe("#/?class=class-1");
  });

  it("selects the next class on ArrowRight", async () => {
    render(<ClassSelector classes={THREE} panelId="panel" />);
    await userEvent.tab();

    await userEvent.keyboard("{ArrowRight}");

    expect(selectedTabName()).toBe("class_1");
  });

  it("moves focus with the selection", async () => {
    render(<ClassSelector classes={THREE} panelId="panel" />);
    await userEvent.tab();

    await userEvent.keyboard("{ArrowRight}");

    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "class_1" }));
  });

  it("wraps from the first class to the last on ArrowLeft", async () => {
    render(<ClassSelector classes={THREE} panelId="panel" />);
    await userEvent.tab();

    await userEvent.keyboard("{ArrowLeft}");

    expect(selectedTabName()).toBe("class_2");
  });

  it("wraps from the last class to the first on ArrowRight", async () => {
    window.history.replaceState(null, "", "#/?class=class-2");
    render(<ClassSelector classes={THREE} panelId="panel" />);
    await userEvent.tab();

    await userEvent.keyboard("{ArrowRight}");

    expect(selectedTabName()).toBe("class_0");
  });

  it("selects the last class on End", async () => {
    render(<ClassSelector classes={THREE} panelId="panel" />);
    await userEvent.tab();

    await userEvent.keyboard("{End}");

    expect(selectedTabName()).toBe("class_2");
  });

  it("selects the first class on Home", async () => {
    window.history.replaceState(null, "", "#/?class=class-2");
    render(<ClassSelector classes={THREE} panelId="panel" />);
    await userEvent.tab();

    await userEvent.keyboard("{Home}");

    expect(selectedTabName()).toBe("class_0");
  });

  it("keeps the other hash parameters", async () => {
    window.history.replaceState(null, "", "#/?dangerous=1");
    render(<ClassSelector classes={THREE} panelId="panel" />);
    await userEvent.tab();

    await userEvent.keyboard("{End}");

    expect(window.location.hash).toBe("#/?dangerous=1&class=class-2");
  });
});

describe("as a drop-down", () => {
  it("shows a labelled drop-down for more than six classes", () => {
    render(<ClassSelector classes={MANY} panelId="panel" />);

    expect(screen.getByRole("combobox", { name: "Alert class" }).tagName).toBe("SELECT");
  });

  it("shows no tabs for more than six classes", () => {
    render(<ClassSelector classes={MANY} panelId="panel" />);

    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("selects the class the hash names", () => {
    window.history.replaceState(null, "", "#/?class=class-5");
    render(<ClassSelector classes={MANY} panelId="panel" />);

    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("class-5");
  });

  it("stores the chosen class's anchor in the hash", async () => {
    render(<ClassSelector classes={MANY} panelId="panel" />);

    await userEvent.selectOptions(screen.getByRole("combobox"), "class_4");

    expect(window.location.hash).toBe("#/?class=class-4");
  });
});

it("renders nothing when there are no classes", () => {
  const { container } = render(<ClassSelector classes={[]} panelId="panel" />);

  expect(container.childElementCount).toBe(0);
});

describe("ClassPanel at the most classes tabs hold", () => {
  const SIX = createClasses(MAX_CLASS_TABS);

  function renderPanel() {
    return render(
      <ClassPanel classes={SIX} selected={SIX[0]!} panelId="panel">
        content
      </ClassPanel>,
    );
  }

  it("is a tab panel", () => {
    renderPanel();

    expect(screen.queryByRole("tabpanel")).not.toBeNull();
  });

  it("is named by its class's tab", () => {
    renderPanel();

    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(getClassTabId("class-0"));
  });
});
