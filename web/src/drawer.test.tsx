// M7 frontend review fixes for the Drawer (D1–D4): focus, unsaved changes, backdrop, focus trap.
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Drawer } from "./components/Drawer";

function ParentWithForm({ dirty = false, onClose = vi.fn() }) {
  const [name, setName] = useState(""); // state in the parent: every keystroke re-renders it
  return (
    <>
      <button type="button">Open</button>
      <Drawer title="New character" onClose={() => onClose()} dirty={dirty || name !== ""}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="button">Save</button>
      </Drawer>
    </>
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Drawer", () => {
  it("D1: typing in a form whose state lives in the parent keeps focus and every letter", async () => {
    render(<ParentWithForm />);
    const input = screen.getByLabelText("Name");
    await userEvent.click(input);
    await userEvent.keyboard("Thorin");
    expect(input).toHaveValue("Thorin");
    expect(input).toHaveFocus();
  });

  it("D2: closing with unsaved changes asks first", async () => {
    const onClose = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<ParentWithForm onClose={onClose} />);
    await userEvent.type(screen.getByLabelText("Name"), "Thorin");
    await userEvent.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("D2: closing without changes doesn't ask", async () => {
    const onClose = vi.fn();
    const confirm = vi.spyOn(window, "confirm");
    render(<ParentWithForm onClose={onClose} />);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("D3: a drag that starts inside the panel and ends on the backdrop doesn't close it", () => {
    const onClose = vi.fn();
    const { container } = render(<ParentWithForm onClose={onClose} />);
    const backdrop = container.querySelector(".drawer-backdrop")!;
    fireEvent.mouseDown(screen.getByLabelText("Name"));
    fireEvent.mouseUp(backdrop);
    fireEvent.click(backdrop);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(backdrop);
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("D4: Tab and Shift+Tab stay inside the dialog", async () => {
    render(<ParentWithForm />);
    const close = screen.getByRole("button", { name: "Close" });
    const save = screen.getByRole("button", { name: "Save" });
    save.focus();
    await userEvent.tab();
    expect(close).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(save).toHaveFocus();
  });
});
