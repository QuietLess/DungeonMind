// M9d S2: the sign-up form asks for an invite code when the hub is invite-only.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionContext } from "./hooks";
import { AuthPage } from "./pages/AuthPage";

const api = vi.hoisted(() => ({ authConfig: vi.fn(), register: vi.fn(), login: vi.fn() }));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

function renderAuth() {
  render(
    <SessionContext.Provider value={{ user: null, setUser: vi.fn() }}>
      <MemoryRouter>
        <AuthPage />
      </MemoryRouter>
    </SessionContext.Provider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  api.register.mockResolvedValue({ id: "u1", username: "mia_r" });
});

describe("sign-up with an invite", () => {
  it("asks for the invite code and sends it", async () => {
    api.authConfig.mockResolvedValue({ signup: "invite" });
    renderAuth();
    await userEvent.click(screen.getByRole("button", { name: /create an account/i }));
    await userEvent.type(screen.getByLabelText("Username"), "mia_r");
    await userEvent.type(screen.getByLabelText("Password"), "a long password");
    await userEvent.type(await screen.findByLabelText("Invite code"), " kq7h3m9xw2ab ");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(api.register).toHaveBeenCalledWith("mia_r", "a long password", "kq7h3m9xw2ab");
  });

  it("open sign-up has no invite field", async () => {
    api.authConfig.mockResolvedValue({ signup: "open" });
    renderAuth();
    await userEvent.click(screen.getByRole("button", { name: /create an account/i }));
    await screen.findByLabelText("Username");
    expect(screen.queryByLabelText("Invite code")).toBeNull();
    await userEvent.type(screen.getByLabelText("Username"), "mia_r");
    await userEvent.type(screen.getByLabelText("Password"), "a long password");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(api.register).toHaveBeenCalledWith("mia_r", "a long password", undefined);
  });
});

describe("first account on an invite-only hub", () => {
  it("doesn't require a code when the hub is empty", async () => {
    api.authConfig.mockResolvedValue({ signup: "invite", first: true });
    renderAuth();
    await userEvent.click(screen.getByRole("button", { name: /create an account/i }));
    await userEvent.type(screen.getByLabelText("Username"), "dm_dana");
    await userEvent.type(screen.getByLabelText("Password"), "a long password");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(api.register).toHaveBeenCalledWith("dm_dana", "a long password", undefined);
  });
});
