"""
Hold-to-record global hotkeys, intercepted at the OS level.

Excerpt from the voice client (`aihylu.py`), trimmed for publication.

The hard part is not reading the key — it is *swallowing* it. A passive listener
(pynput) still lets the chord reach the focused app, so browsers answer
Ctrl+Shift+S with an error beep on every recording. A CGEventTap inserted at the
head of the session event stream can rewrite the event to kCGEventNull, which
makes it disappear before any application sees it.

Modes, selected by the final key of the chord:

    Ctrl+Shift+Q  quick       voice -> native macOS STT -> paste (no LLM)
    Ctrl+Shift+V  structure   voice -> structured text -> clipboard
    Ctrl+Shift+D  dictate     voice -> raw text -> paste into active field
    Ctrl+Shift+S  screen_qa   screenshot + voice -> answer
    Ctrl+Shift+A  agent       screenshot + voice -> tool-using agent acts

Requires Accessibility permission. Falls back to a passive listener without it.
"""

import threading

import Quartz as Q

# Carbon virtual key codes — modifier-independent, unlike characters.
VK_TO_MODE = {12: "quick", 9: "structure", 2: "dictate", 1: "screen_qa", 0: "agent"}
CTRL_SHIFT = Q.kCGEventFlagMaskControl | Q.kCGEventFlagMaskShift


def setup_hotkeys(start_rec, stop_rec, is_recording, on_fallback):
    # Single-slot mutable cell: which vk currently holds the recording open.
    active_vk = [None]

    def _tap_callback(proxy, event_type, event, refcon):
        try:
            flags = Q.CGEventGetFlags(event)

            # Modifier released mid-recording -> treat as key-up.
            if event_type == Q.kCGEventFlagsChanged:
                if is_recording() and active_vk[0] is not None:
                    if (flags & CTRL_SHIFT) != CTRL_SHIFT:
                        active_vk[0] = None
                        threading.Thread(target=stop_rec, daemon=True).start()
                return event

            if event_type == Q.kCGEventKeyDown and (flags & CTRL_SHIFT) == CTRL_SHIFT:
                vk = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
                if vk in VK_TO_MODE:
                    # Start only on the *first* press. macOS emits ~10 KeyDown
                    # events per second while the key is held.
                    if active_vk[0] is None and not is_recording():
                        active_vk[0] = vk
                        threading.Thread(
                            target=start_rec, args=(VK_TO_MODE[vk],), daemon=True
                        ).start()
                    # Suppress every press including key-repeat, or the host app
                    # beeps once per repeat.
                    Q.CGEventSetType(event, Q.kCGEventNull)
                    return event

            if event_type == Q.kCGEventKeyUp:
                vk = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
                if vk == active_vk[0]:
                    active_vk[0] = None
                    if is_recording():
                        threading.Thread(target=stop_rec, daemon=True).start()
                    Q.CGEventSetType(event, Q.kCGEventNull)
                    return event
        except Exception:
            # A raising tap callback gets the tap disabled by the OS.
            # Swallow and pass the event through untouched.
            pass
        return event

    mask = (
        Q.CGEventMaskBit(Q.kCGEventKeyDown)
        | Q.CGEventMaskBit(Q.kCGEventKeyUp)
        | Q.CGEventMaskBit(Q.kCGEventFlagsChanged)
    )
    tap = Q.CGEventTapCreate(
        Q.kCGSessionEventTap,
        Q.kCGHeadInsertEventTap,     # ahead of other taps -> we see it first
        Q.kCGEventTapOptionDefault,  # active tap: may modify/suppress events
        mask,
        _tap_callback,
        None,
    )
    if tap is None:
        # No Accessibility permission. Degrade instead of dying.
        on_fallback(active_vk, VK_TO_MODE)
        return

    src = Q.CFMachPortCreateRunLoopSource(None, tap, 0)
    # Must be the *main* run loop: the webview UI owns that thread.
    Q.CFRunLoopAddSource(Q.CFRunLoopGetMain(), src, Q.kCFRunLoopCommonModes)
    Q.CGEventTapEnable(tap, True)
