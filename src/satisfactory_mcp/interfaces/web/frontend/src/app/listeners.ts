/* A listener list: `on` adds one, `emit` calls each in the order they were added. Imports
 * nothing, so any module can own one without joining a ring. */

export interface Listeners<A extends unknown[]> {
  on: (listener: (...args: A) => void) => void;
  emit: (...args: A) => void;
}

export function createListeners<A extends unknown[] = []>(): Listeners<A> {
  var listeners: Array<(...args: A) => void> = [];
  return {
    on: function (listener) {
      listeners.push(listener);
    },
    emit: function (...args) {
      listeners.forEach(function (listener) {
        listener(...args);
      });
    },
  };
}
