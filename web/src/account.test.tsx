// M9b AU1–AU4: the account page.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { TopBar } from "./components/TopBar";
import { SessionContext } from "./hooks";
import { AccountPage } from "./pages/AccountPage";

const api = vi.hoisted(() => ({
  account: vi.fn(),
  changePassword: vi.fn(),
  signOutOthers: vi.fn(),
  deleteAccount: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

const user = { id: "u1", username: "mia_r" };
const summary = {
  username: "mia_r",
  created_at: "2026-10-16T12:00:00+00:00",
  sessions: 3,
  questions_today: 4,
  daily_limit: 50,
};

function renderPage(setUser = vi.fn()) {
  render(
    <SessionContext.Provider value={{ user, setUser }}>
      <MemoryRouter initialEntries={["/account"]}>
        <Routes>
          <Route path="/account" element={<AccountPage />} />
          <Route path="/login" element={<p>sign-in page</p>} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
  return setUser;
}

const section = (name: RegExp) => screen.getByRole("region", { name });

beforeEach(() => {
  vi.resetAllMocks();
  api.account.mockResolvedValue(summary);
});

describe("account page", () => {
  it("AU1: the top bar's username links to the account page", () => {
    render(
      <SessionContext.Provider value={{ user, setUser: vi.fn() }}>
        <MemoryRouter>
          <TopBar />
        </MemoryRouter>
      </SessionContext.Provider>,
    );
    expect(screen.getByRole("link", { name: "mia_r" })).toHaveAttribute("href", "/account");
  });

  it("AU1: shows the username, usage and sessions", async () => {
    renderPage();
    expect(await screen.findByText("4 of 50")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /mia_r/ })).toBeInTheDocument();
  });

  it("AU2: a mismatched repeat is caught before sending", async () => {
    renderPage();
    const form = section(/change password/i);
    await userEvent.type(within(form).getByLabelText("Current password"), "old password 1");
    await userEvent.type(within(form).getByLabelText("New password"), "new password 12");
    await userEvent.type(within(form).getByLabelText("Repeat new password"), "new password 13");
    await userEvent.click(within(form).getByRole("button", { name: "Change password" }));
    expect(within(form).getByRole("alert")).toHaveTextContent(/don't match/i);
    expect(api.changePassword).not.toHaveBeenCalled();
  });

  it("AU2: success and server errors are shown", async () => {
    api.changePassword.mockRejectedValueOnce(new ApiError(403, "Current password is wrong."));
    api.changePassword.mockResolvedValueOnce(undefined);
    renderPage();
    const form = section(/change password/i);
    await userEvent.type(within(form).getByLabelText("Current password"), "old password 1");
    await userEvent.type(within(form).getByLabelText("New password"), "new password 12");
    await userEvent.type(within(form).getByLabelText("Repeat new password"), "new password 12");
    await userEvent.click(within(form).getByRole("button", { name: "Change password" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Current password is wrong.");
    await userEvent.click(within(form).getByRole("button", { name: "Change password" }));
    expect(await within(form).findByRole("status")).toHaveTextContent(/other devices/i);
    expect(api.changePassword).toHaveBeenLastCalledWith("old password 1", "new password 12");
  });

  it("AU3: signing out other devices says how many", async () => {
    api.signOutOthers.mockResolvedValue({ signed_out: 2 });
    renderPage();
    const box = section(/devices/i);
    await userEvent.click(await within(box).findByRole("button", { name: /sign out other devices/i }));
    expect(await within(box).findByRole("status")).toHaveTextContent("2");
  });

  it("AU4: delete stays disabled until the username matches, then signs out", async () => {
    api.deleteAccount.mockResolvedValue(undefined);
    const setUser = renderPage();
    const box = section(/delete account/i);
    const button = within(box).getByRole("button", { name: "Delete my account" });
    await userEvent.type(within(box).getByLabelText("Password"), "my password 12");
    await userEvent.type(within(box).getByLabelText(/type your username/i), "mia");
    expect(button).toBeDisabled();
    await userEvent.type(within(box).getByLabelText(/type your username/i), "_r");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(api.deleteAccount).toHaveBeenCalledWith("my password 12", "mia_r");
    expect(await screen.findByText("sign-in page")).toBeInTheDocument();
    expect(setUser).toHaveBeenCalledWith(null);
  });

  it("AU4: a DM who still has campaigns sees the reason", async () => {
    api.deleteAccount.mockRejectedValue(
      new ApiError(409, "You're the DM of 1 campaign. Delete them first."),
    );
    renderPage();
    const box = section(/delete account/i);
    await userEvent.type(within(box).getByLabelText("Password"), "my password 12");
    await userEvent.type(within(box).getByLabelText(/type your username/i), "mia_r");
    await userEvent.click(within(box).getByRole("button", { name: "Delete my account" }));
    expect(await within(box).findByRole("alert")).toHaveTextContent("DM of 1 campaign");
  });
});
