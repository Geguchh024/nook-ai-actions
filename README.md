# AI Actions

> Select text anywhere, press **Ctrl+Alt+I**, and rewrite, fix, shorten,
> summarize, translate or reply to it with the AI model of your choice.
> Then paste the result straight back.

![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![Python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![Version](https://img.shields.io/badge/version-1.1.0-7C5CFF)
![Plugin for](https://img.shields.io/badge/plugin%20for-Nook%20%2F%20Fenn-111)

A plugin for **Nook**, the floating notch for Windows (and its successor
**Fenn**). It runs on Python's standard library, so there is nothing to `pip install`.

| | |
|---|---|
| **Hotkey** | `Ctrl+Alt+I` |
| **Tile** | small |
| **Page** | 620 × 400 |
| **Providers** | Anthropic Claude (default), any OpenAI-compatible API, Ollama, LM Studio |
| **Dependencies** | none |

## Highlights

- **Works in any app.** It grabs the selection with a simulated `Ctrl+C` and
  restores your clipboard afterwards.
- **One-click actions:** Rewrite, Fix grammar, Make shorter, Summarize,
  Translate, Reply. You can also type your own instruction.
- **Streaming results** that you can **paste back** into the original window,
  copy, retry, or refine with another action.
- **Shares provider settings with [AI Chat](https://github.com/Geguchh024/nook-ai-chat).**
  Set your key once and both plugins use it.
- **Private by design.** Text is only sent when you press an action, and the
  API key is encrypted with Windows DPAPI.

## Install

Clone the repo into your plugins folder. The folder name must be `ai-actions`:

```powershell
# Nook
git clone https://github.com/Geguchh024/nook-ai-actions "$env:APPDATA\Nook\plugins\ai-actions"
# Fenn
git clone https://github.com/Geguchh024/nook-ai-actions "$env:APPDATA\Fenn\plugins\ai-actions"
```

You can also download the ZIP and extract it to the same place. The tile
appears on the next reload. Then open the page, press the gear button and add
an API key, or point it at a local model.

## How to use it

Select text in any app and press **Ctrl+Alt+I**. The page opens with your
text and these actions:

- **Rewrite**, **Fix grammar**, **Make shorter**, **Summarize**
- **Translate** into the language picked below the buttons
- **Reply** in the tone picked below the buttons
- Anything else: type an instruction ("make it more formal", "turn into a
  bullet list") in the box under the buttons and press Enter

If nothing is selected, the plugin uses the text on the clipboard. With no
text at all, you can type or paste it on the page, or take it from the
clipboard. The x button clears it.

The result opens on its own page and streams in. From there you can:

| Button | What it does |
|---|---|
| **Paste back** | Switches to the window the selection came from, pastes with `Ctrl+V` and restores your clipboard |
| **Copy** | Copies the result |
| **Try again** | Runs the same action again |
| Language / tone | Changing either one redoes a translation or reply |
| **Refine** (arrow) | Uses the result as the input for another action |
| **Back** | Returns to the actions |

## Provider

The gear button opens the same provider settings as
[AI Chat](https://github.com/Geguchh024/nook-ai-chat): Anthropic (the
default), an OpenAI-compatible endpoint, or a local Ollama or LM Studio
server.

`llm.py` is shared by copying, so both plugins hold identical copies. Each
plugin reads the newest `provider.json` from either plugin's data folder, so a
key set in one works in the other. The key is encrypted with Windows DPAPI for
your user and is never stored in `plugin.toml`.

## Privacy: what is sent where

- **Sent:** only the captured text and the chosen instruction, to the base
  URL you configured, at the moment you press an action.
- **Stored:** your language and tone choices (`prefs.json`) and the provider
  settings. Nothing else.

## Files

| File | What it is |
|---|---|
| `main.py` | Pages, actions and events |
| `llm.py` | Provider settings and streaming client, shared with AI Chat |
| `clip.py` | Capturing the selection, restoring the clipboard and pasting back |
| `nook.py` | The Nook plugin helper |
| `plugin.toml` | Plugin manifest |

## Requirements

- Windows 10 or 11
- Python 3.8+ on `PATH` as `python`
- An API key for Anthropic or an OpenAI-compatible service, or a local
  Ollama or LM Studio server
