// Reads a JSON list of TeX strings on stdin, prints a JSON list of KaTeX HTML (display mode, no MathML copy).
const katex = require("katex");
let inp = "";
process.stdin.on("data", d => inp += d).on("end", () => {
  const items = JSON.parse(inp);
  console.log(JSON.stringify(items.map(t => katex.renderToString(t, { displayMode: true, throwOnError: true, output: "html" }))));
});
