import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// eslint-config-next 16 ships native flat configs; FlatCompat is no longer needed.
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // New React Compiler rules enabled by eslint-config-next 16. They flag patterns in code that
    // predates them (9 setState-in-effect, 3 ref reads during render) and was verified in a
    // browser. Warn, not fail, until those spots are refactored and re-checked in the UI.
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
    },
  },
  globalIgnores([".next/**", "public/**", "next-env.d.ts"]),
]);
