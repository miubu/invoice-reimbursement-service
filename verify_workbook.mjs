import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const previewPath = process.argv[3];
if (!inputPath || !previewPath) {
  throw new Error("Usage: node verify_workbook.mjs <input.xlsx> <preview.png>");
}

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const table = await workbook.inspect({
  kind: "table",
  range: "发票报销填报表!A1:J6",
  include: "values,formulas",
  tableMaxRows: 10,
  tableMaxCols: 10,
});
console.log(table.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({
  sheetName: "发票报销填报表",
  range: "A1:J6",
  scale: 1.5,
  format: "png",
});
await fs.mkdir(path.dirname(previewPath), { recursive: true });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
