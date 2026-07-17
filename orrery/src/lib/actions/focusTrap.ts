// Shared modal focus-trap Svelte action (WCAG 2.4.3 + 2.1.2). Previously copy-pasted three times
// (TuningConsole / DecisionSheet / HelpOverlay): capture the triggering element on open, move
// focus INTO the dialog, trap Tab/Shift+Tab so focus never escapes it, Escape invokes onClose,
// and restore focus to the trigger on teardown.
//
// Usage: `<div role="dialog" tabindex="-1" use:focusTrap={{ onClose }}>...</div>`. By default the
// FIRST focusable descendant gets initial focus (falling back to the dialog container itself when
// there isn't one). Pass `initialFocus` to land focus somewhere specific instead (e.g.
// DecisionSheet wants its textarea, not whichever button happens to be first in DOM order); when
// `initialFocus()` returns `null` (e.g. the field isn't rendered in an observe-only state), focus
// falls back to the dialog container — NOT to "first focusable" — matching the original
// per-component behavior this replaces.

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusable(node: HTMLElement): HTMLElement[] {
  return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

export interface FocusTrapOptions {
  onClose: () => void;
  /** Explicit initial-focus target; omit to default to "first focusable, else the container". */
  initialFocus?: () => HTMLElement | null;
}

export function focusTrap(node: HTMLElement, opts: FocusTrapOptions) {
  let options = opts;
  const triggerEl = document.activeElement as HTMLElement | null;

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      options.onClose();
      return;
    }
    if (e.key !== 'Tab') return;
    const items = focusable(node);
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement as HTMLElement | null;
    // `active === node` is the case where initialFocus() returned null and focus landed on the
    // dialog container itself: it sits BEFORE the first focusable item, so a Shift+Tab from here
    // must wrap to the last item, not escape the dialog. Treat it as the first boundary.
    if (e.shiftKey && (active === first || active === node || !node.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  node.addEventListener('keydown', onKeydown);

  // Move focus into the dialog on open.
  queueMicrotask(() => {
    const target = options.initialFocus
      ? options.initialFocus() ?? node
      : focusable(node)[0] ?? node;
    target.focus();
  });

  return {
    update(next: FocusTrapOptions) {
      options = next;
    },
    destroy() {
      node.removeEventListener('keydown', onKeydown);
      triggerEl?.focus?.();
    },
  };
}
