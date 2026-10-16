/* A native <dialog> as a modal: Esc, the × and a backdrop click all go through one close
 * callback, so the caller can keep the address in step; focus returns to the opener. */

export interface Modal {
  open(opener?: HTMLElement | null): void;
  close(): void;
  isOpen(): boolean;
}

export function modal(dialog: HTMLDialogElement, requestClose: () => void): Modal {
  let opener: HTMLElement | null = null;
  dialog.addEventListener("cancel", function (event) {
    event.preventDefault();
    requestClose();
  });
  dialog.addEventListener("click", function (event) {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    const outside = event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom;
    if (outside) requestClose();
  });
  return {
    open: function (from) {
      opener = from || (document.activeElement as HTMLElement | null);
      if (!dialog.open) dialog.showModal();
    },
    close: function () {
      if (!dialog.open) return;
      dialog.close();
      if (opener && opener.isConnected) opener.focus();
      opener = null;
    },
    isOpen: function () {
      return dialog.open;
    },
  };
}
