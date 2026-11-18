// PDF report from the engine's report sections (report.build_sections), drawn with jsPDF.
import { jsPDF } from "jspdf";
import { autoTable } from "jspdf-autotable";

const ascii = (s) => String(s ?? "").replace(/[–—]/g, "-").replace(/[^\x20-\x7E]/g, "?");

export function buildPdf(result, validationRows) {
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const W = doc.internal.pageSize.getWidth();
  doc.setFont("helvetica", "bold"); doc.setFontSize(16);
  doc.text(ascii(`Orbital Intelligence report: ${result.name}`), 36, 44);
  doc.setFont("helvetica", "italic"); doc.setFontSize(8.5);
  doc.text(ascii(`Engine v${result.engine_version}. All values computed by the simulation engine for the configuration shown. Generated ${new Date().toISOString().slice(0, 19)}Z.`), 36, 60);
  let y = 76;
  const sections = [...result.exports.sections];
  if (validationRows?.length) {
    sections.push(["Numerical validation", [["Check", "Value", "Threshold", "Result"],
      ...validationRows.map((r) => [r.check, r.value.toExponential(3), r.threshold.toExponential(1), r.passed ? "PASS" : "FAIL"])]]);
  }
  for (const [head, rows] of sections) {
    if (y > 760) { doc.addPage(); y = 44; }
    doc.setFont("helvetica", "bold"); doc.setFontSize(11);
    doc.text(ascii(head), 36, y);
    autoTable(doc, {
      startY: y + 6, head: [rows[0].map(ascii)], body: rows.slice(1).map((r) => r.map(ascii)),
      margin: { left: 36, right: 36 }, styles: { fontSize: 7.5, cellPadding: 3, overflow: "linebreak" },
      headStyles: { fillColor: [11, 60, 93], textColor: 255 }, tableWidth: W - 72,
      columnStyles: rows[0].length === 2 ? { 0: { cellWidth: (W - 72) * 0.4 } } : {},
    });
    y = doc.lastAutoTable.finalY + 22;
  }
  return doc.output("blob");
}
