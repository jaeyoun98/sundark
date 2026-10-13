# Subscription access only, and no tools on requests that carry paper content

Sundark reaches models only through the user's subscription login in each AI Backend (Claude
Code, Hermes, ChatGPT OAuth), never through a paid API key: the Claude Code path strips `ANTHROPIC_API_KEY`, and
Sundark never changes a Hermes Provider. Every request that carries PDF content runs with all
agent tools disabled, because PDF text and images are untrusted and could carry prompt injection.
This includes equation reading: the equation image is sent inline in the message (verified to work
with tools off on both Claude Code and Hermes) instead of enabling a file-read tool to open it.

## Considered Options

- **Enable Claude Code's Read tool, scoped to the image's directory, for equation reading.** This is
  what the first implementation did. Rejected because it is the one place a tool would be live on a
  request derived from untrusted input, and the inline-image path makes it unnecessary.

ChatGPT uses its own dynamic OAuth registration with PKCE and verified OIDC
identity, never Codex credentials. Credentials stay outside reader data and are
never exposed in status or config responses. Returning authorization uses the
saved ID token as an OIDC hint in the OpenAI sign-in URL; access and refresh tokens
remain server-side. Requests use the public Responses endpoint with
`tools: []`, `tool_choice: "none"`, `store: false`, and `stream: true`; equation
images remain inline. The transport requires completed text output before a
result can be cached. Login does not prove subscription inference eligibility.
