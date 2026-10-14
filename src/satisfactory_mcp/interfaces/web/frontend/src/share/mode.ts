/** True in the map-only build (`vite build --mode share`); the bundler drops the other branch. */
export const SHARE = import.meta.env.MODE === "share";
