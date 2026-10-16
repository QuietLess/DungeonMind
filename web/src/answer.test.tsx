// UI3 (safe answers, closes H1) and UI4 (citations): docs/ARCHITECTURE.md › M7 contract.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AnswerText } from "./answer";
import type { Citation } from "./types";

const CITES: Citation[] = [
  { id: "N1", kind: "note", label: "Lore › NPC › Elara", entry_id: 7 },
  { id: "E1", kind: "srd", label: "SRD 5.2.1, Grappled, p. 183" },
  { id: "T1", kind: "tool", label: "probability tool" },
];

function show(text: string, citations: Citation[] = CITES, onCitation = vi.fn()) {
  const { container } = render(
    <AnswerText text={text} citations={citations} onCitation={onCitation} />,
  );
  return { container, onCitation };
}

describe("UI3 safe rendering", () => {
  it.each([
    ["markdown link", "See [the docs](http://evil.example/?d=secret) now."],
    ["markdown image", "![x](http://evil.example/p.png?d=secret)"],
    ["bare url", "Visit http://evil.example/?d=secret please"],
    ["raw html", '<img src="http://evil.example/x.png"><a href="http://evil.example">x</a>'],
    ["script", "<script>alert(1)</script>"],
  ])("never produces links, images or HTML from a %s", (_name, text) => {
    const { container } = show(text);
    expect(container.querySelector("a, img, script, iframe")).toBeNull();
    expect(container.textContent).toContain(text.includes("<") ? "<" : "evil.example");
  });

  it("renders paragraphs, line breaks, lists and bold", () => {
    const { container } = show("First **bold** line\nsecond line\n\n- one\n- two\n\n1. a\n2. b");
    expect(container.querySelectorAll("p").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("br")).not.toBeNull();
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
  });
});

describe("UI4 citations", () => {
  it("turns known markers into buttons and calls back with the citation", async () => {
    const { onCitation } = show("Elara is a spy [N1]. Grappling works so [E1, T1].");
    const buttons = screen.getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["N1", "E1", "T1"]);
    await userEvent.click(screen.getByRole("button", { name: /N1/ }));
    expect(onCitation).toHaveBeenCalledWith(CITES[0]);
    await userEvent.click(screen.getByRole("button", { name: /E1/ }));
    expect(onCitation).toHaveBeenLastCalledWith(CITES[1]);
  });

  it("buttons describe their source for screen readers", () => {
    show("Spy [N1].");
    expect(screen.getByRole("button", { name: /Lore › NPC › Elara/ })).toBeInTheDocument();
  });

  it("leaves markers without a matching citation as plain text", () => {
    const { container } = show("Invented [N9] and [E1].");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(container.textContent).toContain("[N9]");
  });
});
