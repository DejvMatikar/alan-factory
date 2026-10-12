import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../test/server";
import CredentialsSection from "./CredentialsSection";

function secretsResponse(
  secrets: { name: string; fingerprint: string; last_verified?: string | null; expires_at?: string | null }[],
) {
  return {
    secrets: secrets.map((s) => ({
      name: s.name,
      fingerprint: s.fingerprint,
      last_verified: s.last_verified ?? null,
      expires_at: s.expires_at ?? null,
      expiry_warned_at: null,
    })),
  };
}

describe("CredentialsSection", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("shows the empty state when there are no credentials", async () => {
    server.use(http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))));
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toHaveTextContent("No credentials yet.");
    });
  });

  it("surfaces a list-load failure as a typed error, not a blank panel", async () => {
    server.use(
      http.get("/api/secrets", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-error")).toHaveTextContent("500: boom");
    });
  });

  it("renders name + fingerprint + last_verified/expires_at, and never a value field", async () => {
    server.use(
      http.get("/api/secrets", () =>
        HttpResponse.json(
          secretsResponse([
            {
              name: "acme/widget:transport",
              fingerprint: "abc123",
              last_verified: "2026-07-01T00:00:00Z",
              expires_at: "2027-01-01",
            },
          ]),
        ),
      ),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credential-row-acme/widget:transport-name")).toHaveTextContent(
        "acme/widget:transport",
      );
    });
    const row = screen.getByTestId("credential-row-acme/widget:transport");
    expect(row).toHaveTextContent("fingerprint abc123");
    expect(row).toHaveTextContent("verified 2026-07-01T00:00:00Z");
    expect(row).toHaveTextContent("expires 2027-01-01");
    expect(row.innerHTML).not.toMatch(/ghp_/);
  });

  it("previews the record name live as the user types the repository field and switches purpose", async () => {
    server.use(http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))));
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("credential-preview")).toHaveTextContent("owner/repo:transport");
    const user = userEvent.setup();
    await user.type(screen.getByTestId("credential-engine-input"), "acme/widget");
    expect(screen.getByTestId("credential-preview")).toHaveTextContent("acme/widget:transport");
    await user.selectOptions(screen.getByTestId("credential-purpose-select"), "reviewer");
    expect(screen.getByTestId("credential-preview")).toHaveTextContent("acme/widget:reviewer");
  });

  it("adds a credential: submits engine/purpose/value/expires_at, clears the input, and refetches the list", async () => {
    let createdBody: unknown = null;
    let listCallCount = 0;
    server.use(
      http.get("/api/secrets", () => {
        listCallCount += 1;
        return HttpResponse.json(
          listCallCount === 1
            ? secretsResponse([])
            : secretsResponse([{ name: "acme/widget:transport", fingerprint: "abc123" }]),
        );
      }),
      http.post("/api/secrets", async ({ request }) => {
        createdBody = await request.json();
        return HttpResponse.json(
          { name: "acme/widget:transport", fingerprint: "abc123", last_verified: null, expires_at: null, expiry_warned_at: null },
          { status: 201 },
        );
      }),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("credential-engine-input"), "acme/widget");
    await user.type(screen.getByTestId("credential-value-input"), "ghp_supersecret");
    await user.type(screen.getByTestId("credential-expires-input"), "2027-01-01");
    await user.click(screen.getByTestId("credential-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("credential-row-acme/widget:transport")).toBeInTheDocument();
    });
    expect(createdBody).toEqual({
      engine: "acme/widget",
      purpose: "transport",
      value: "ghp_supersecret",
      expires_at: "2027-01-01",
    });
    expect(screen.getByTestId("credential-value-input")).toHaveValue("");
  });

  it("accepts a pasted repository URL and normalizes it to owner/repo in the request body", async () => {
    let createdBody: unknown = null;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/secrets", async ({ request }) => {
        createdBody = await request.json();
        return HttpResponse.json(
          { name: "acme/widget:transport", fingerprint: "abc123", last_verified: null, expires_at: null, expiry_warned_at: null },
          { status: 201 },
        );
      }),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(
      screen.getByTestId("credential-engine-input"),
      "https://github.com/acme/widget.git",
    );
    expect(screen.getByTestId("credential-preview")).toHaveTextContent("acme/widget:transport");
    await user.type(screen.getByTestId("credential-value-input"), "ghp_supersecret");
    await user.click(screen.getByTestId("credential-submit"));
    await waitFor(() => {
      expect(createdBody).toEqual({
        engine: "acme/widget",
        purpose: "transport",
        value: "ghp_supersecret",
        expires_at: null,
      });
    });
  });

  it("a direct submit with a blank repository field is a silent no-op (no error, no request)", async () => {
    let createCalled = false;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/secrets", () => {
        createCalled = true;
        return HttpResponse.json({ detail: "unreachable" }, { status: 422 });
      }),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    fireEvent.submit(screen.getByTestId("credentials-form"));
    expect(screen.queryByTestId("credential-form-error")).not.toBeInTheDocument();
    expect(createCalled).toBe(false);
  });

  it("blocks submission before any network call when the repository is a non-GitHub host", async () => {
    let createCalled = false;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/secrets", () => {
        createCalled = true;
        return HttpResponse.json({ detail: "unreachable" }, { status: 422 });
      }),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("credential-engine-input"), "https://gitlab.com/acme/widget");
    await user.type(screen.getByTestId("credential-value-input"), "x");
    await user.click(screen.getByTestId("credential-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("credential-form-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("credential-form-error-detail")).toHaveTextContent(
      "Only github.com repositories are supported.",
    );
    expect(createCalled).toBe(false);
  });

  it("blocks submission before any network call when the repository text is unparsable", async () => {
    let createCalled = false;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/secrets", () => {
        createCalled = true;
        return HttpResponse.json({ detail: "unreachable" }, { status: 422 });
      }),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("credential-engine-input"), "not a url at all");
    await user.type(screen.getByTestId("credential-value-input"), "x");
    await user.click(screen.getByTestId("credential-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("credential-form-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("credential-form-error-detail")).toHaveTextContent(
      "Enter a GitHub repository URL or an owner/repo slug.",
    );
    expect(createCalled).toBe(false);
  });

  it("blocks submission before any network call when owner/repo falls outside the URL-safe segment charset", async () => {
    let createCalled = false;
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/secrets", () => {
        createCalled = true;
        return HttpResponse.json({ detail: "unreachable" }, { status: 422 });
      }),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("credential-engine-input"), "acme!bad/widget");
    await user.type(screen.getByTestId("credential-value-input"), "x");
    await user.click(screen.getByTestId("credential-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("credential-form-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("credential-form-error-detail")).toHaveTextContent(
      "Owner and repository may only contain letters, numbers",
    );
    expect(createCalled).toBe(false);
  });

  it("surfaces a validation error (422) from the add form without losing the typed repository field", async () => {
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/secrets", () =>
        HttpResponse.json({ detail: "value must not be empty" }, { status: 422 }),
      ),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("credential-engine-input"), "acme/widget");
    await user.type(screen.getByTestId("credential-value-input"), "x");
    await user.click(screen.getByTestId("credential-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("credential-form-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("credential-engine-input")).toHaveValue("acme/widget");
  });

  it("surfaces a list-shaped validation detail (422) as the status text, no longer the raw JSON body", async () => {
    server.use(
      http.get("/api/secrets", () => HttpResponse.json(secretsResponse([]))),
      http.post("/api/secrets", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "value"], msg: "Field required", type: "missing" }] },
          { status: 422, statusText: "Unprocessable Entity" },
        ),
      ),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(screen.getByTestId("credential-engine-input"), "acme/widget");
    await user.type(screen.getByTestId("credential-value-input"), "x");
    await user.click(screen.getByTestId("credential-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("credential-form-error")).toHaveTextContent("Unprocessable Entity");
    });
    expect(screen.getByTestId("credential-form-error")).not.toHaveTextContent("Field required");
  });

  it("delete: opens a confirm dialog, cancel leaves the record, confirm deletes with engine/purpose split from the name", async () => {
    let deletedBody: unknown = null;
    let listCallCount = 0;
    server.use(
      http.get("/api/secrets", () => {
        listCallCount += 1;
        return HttpResponse.json(
          listCallCount === 1
            ? secretsResponse([{ name: "acme/widget:transport", fingerprint: "abc123" }])
            : secretsResponse([]),
        );
      }),
      http.delete("/api/secrets", async ({ request }) => {
        deletedBody = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credential-row-acme/widget:transport")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("credential-row-acme/widget:transport-delete"));
    const confirmDialog = screen.getByTestId("credential-delete-confirm");
    expect(confirmDialog).toBeInTheDocument();

    await user.click(within(confirmDialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("credential-delete-confirm")).not.toBeInTheDocument();
    expect(screen.getByTestId("credential-row-acme/widget:transport")).toBeInTheDocument();

    await user.click(screen.getByTestId("credential-row-acme/widget:transport-delete"));
    await user.click(
      within(screen.getByTestId("credential-delete-confirm")).getByRole("button", { name: "Delete" }),
    );

    await waitFor(() => {
      expect(screen.getByTestId("credentials-empty")).toBeInTheDocument();
    });
    expect(deletedBody).toEqual({ engine: "acme/widget", purpose: "transport" });
  });

  it("surfaces a delete failure as a typed notice, retaining the record", async () => {
    server.use(
      http.get("/api/secrets", () =>
        HttpResponse.json(secretsResponse([{ name: "acme/widget:transport", fingerprint: "abc123" }])),
      ),
      http.delete("/api/secrets", () =>
        HttpResponse.json({ detail: "secret not found" }, { status: 404 }),
      ),
    );
    render(<CredentialsSection />);
    await waitFor(() => {
      expect(screen.getByTestId("credential-row-acme/widget:transport")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.click(screen.getByTestId("credential-row-acme/widget:transport-delete"));
    await user.click(
      within(screen.getByTestId("credential-delete-confirm")).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("credential-delete-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("credential-row-acme/widget:transport")).toBeInTheDocument();
  });
});
