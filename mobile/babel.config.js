// `babel-preset-expo` handles JSX, TypeScript, and expo-router's file-based routing. Nothing
// custom: a bespoke Babel config in a React Native app is a source of failures that only appear in
// release builds, and there is nothing here that needs one.
module.exports = function (api) {
  api.cache(true);
  return { presets: ["babel-preset-expo"] };
};
