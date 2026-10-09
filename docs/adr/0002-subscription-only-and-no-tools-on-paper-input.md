# Subscription access only, and no tools on requests that carry paper content

Paper Reader reaches models only through the user's subscription login in each AI Backend (Claude
Code, Hermes), never through a paid API key: the Claude Code path strips `ANTHROPIC_API_KEY`, and
Paper Reader never changes a Hermes Provider. Every request that carries PDF content runs with all
agent tools disabled, because PDF text and images are untrusted and could carry prompt injection.
This includes equation reading: the equation image is sent inline in the message (verified to work
with tools off on both Claude Code and Hermes) instead of enabling a file-read tool to open it.

## Considered Options

- **Enable Claude Code's Read tool, scoped to the image's directory, for equation reading.** This is
  what the first implementation did. Rejected because it is the one place a tool would be live on a
  request derived from untrusted input, and the inline-image path makes it unnecessary.
