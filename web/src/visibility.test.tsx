// UI6 visibility is always visible: docs/ARCHITECTURE.md › M7 contract.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EntryCard } from "./components/EntryCard";
import type { Entry } from "./types";
import { VisibilityBadge, VisibilityPicker, visibilityOptions } from "./visibility";

describe("VisibilityBadge", () => {
  it.each([
    ["private", "Private"],
    ["dm", "Shared with DM"],
    ["party", "Party"],
  ] as const)("shows %s as text with an icon, not colour alone", (v, label) => {
    const { container } = render(<VisibilityBadge visibility={v} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(container.querySelector("svg[aria-hidden='true']")).not.toBeNull();
  });
});

describe("visibilityOptions", () => {
  it("players writing journal or character entries get all three", () => {
    expect(visibilityOptions("journal", "player")).toEqual(["private", "dm", "party"]);
    expect(visibilityOptions("character", "player")).toEqual(["private", "dm", "party"]);
  });
  it("the DM and the lore board get private and party only", () => {
    expect(visibilityOptions("journal", "dm")).toEqual(["private", "party"]);
    expect(visibilityOptions("lore", "dm")).toEqual(["private", "party"]);
  });
});

describe("VisibilityPicker", () => {
  it("is a labelled radio group that reports changes", async () => {
    const onChange = vi.fn();
    render(<VisibilityPicker board="journal" role="player" value="party" onChange={onChange} />);
    expect(screen.getByRole("radiogroup", { name: /who can see this/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Party/ })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /Shared with DM/ }));
    expect(onChange).toHaveBeenCalledWith("dm");
  });
});

const lore: Entry = {
  id: 3, board: "lore", character_id: null, author_id: "dm1", kind: "npc",
  title: "Baron Velkor", body: "Secretly a vampire.", visibility: "private", session: null,
  created_at: "2026-10-16T12:00:00+00:00", updated_at: "2026-10-16T12:00:00+00:00",
};

describe("EntryCard reveal", () => {
  it("offers Reveal to the DM on private lore and asks before revealing", async () => {
    const onReveal = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<EntryCard entry={lore} role="dm" userId="dm1" onOpen={vi.fn()} onReveal={onReveal} />);
    const button = screen.getByRole("button", { name: /reveal to party/i });
    await userEvent.click(button);
    expect(onReveal).not.toHaveBeenCalled();
    await userEvent.click(button);
    expect(onReveal).toHaveBeenCalledWith(lore);
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["a player", { role: "player" as const, entry: lore }],
    ["revealed lore", { role: "dm" as const, entry: { ...lore, visibility: "party" as const } }],
    ["a journal entry", { role: "dm" as const, entry: { ...lore, board: "journal" as const } }],
  ])("no Reveal button for %s", (_name, { role, entry }) => {
    render(<EntryCard entry={entry} role={role} userId="dm1" onOpen={vi.fn()} onReveal={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
  });

  it("shows title, kind and the visibility badge, and opens on click", async () => {
    const onOpen = vi.fn();
    render(<EntryCard entry={lore} role="dm" userId="dm1" onOpen={onOpen} onReveal={vi.fn()} />);
    expect(screen.getByText("Private")).toBeInTheDocument();
    expect(screen.getByText(/NPC/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Baron Velkor/ }));
    expect(onOpen).toHaveBeenCalledWith(lore);
  });
});

describe("M9f reveal warning", () => {
  it("warns when an entry still has a DM section", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const entry: Entry = {
      id: 1, board: "lore", character_id: null, author_id: "u1", kind: "item",
      title: "Madalyon", body: "Eski.\n\n## DM notu\nGizli.", visibility: "private",
      session: null, created_at: "", updated_at: "",
    };
    render(<EntryCard entry={entry} role="dm" userId="u1" onOpen={() => {}} onReveal={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /reveal/i }));
    expect(confirm.mock.calls[0][0]).toMatch(/DM notu/);
  });
});
