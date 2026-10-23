/* Offline OOXML export. Text cells use inlineStr, never spreadsheet formulas. */
(function (global) {
  'use strict';
  const encoder = new TextEncoder();
  function xml(value) {
    return String(value ?? '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[ch]);
  }
  function col(index) { let value = ''; for (index++; index; index = Math.floor((index - 1) / 26)) value = String.fromCharCode(65 + (index - 1) % 26) + value; return value; }
  const crcTable = Array.from({ length: 256 }, (_, n) => { for (let i = 0; i < 8; i++) n = (n & 1) ? 0xEDB88320 ^ (n >>> 1) : n >>> 1; return n >>> 0; });
  function crc32(bytes) { let crc = 0xFFFFFFFF; for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8); return (crc ^ 0xFFFFFFFF) >>> 0; }
  function join(parts) { const result = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0)); let offset = 0; for (const part of parts) { result.set(part, offset); offset += part.length; } return result; }
  function header(size) { const bytes = new Uint8Array(size); return { bytes, view: new DataView(bytes.buffer) }; }
  function zip(files) {
    const entries = [], central = []; let offset = 0;
    for (const [path, content] of Object.entries(files)) {
      const name = encoder.encode(path), data = encoder.encode(content), crc = crc32(data), local = header(30);
      local.view.setUint32(0, 0x04034b50, true); local.view.setUint16(4, 20, true); local.view.setUint16(6, 0x800, true);
      local.view.setUint32(14, crc, true); local.view.setUint32(18, data.length, true); local.view.setUint32(22, data.length, true); local.view.setUint16(26, name.length, true);
      entries.push(local.bytes, name, data);
      const directory = header(46);
      directory.view.setUint32(0, 0x02014b50, true); directory.view.setUint16(4, 20, true); directory.view.setUint16(6, 20, true); directory.view.setUint16(8, 0x800, true);
      directory.view.setUint32(16, crc, true); directory.view.setUint32(20, data.length, true); directory.view.setUint32(24, data.length, true); directory.view.setUint16(28, name.length, true); directory.view.setUint32(42, offset, true);
      central.push(directory.bytes, name); offset += local.bytes.length + name.length + data.length;
    }
    const directoryBytes = join(central), end = header(22), count = Object.keys(files).length;
    end.view.setUint32(0, 0x06054b50, true); end.view.setUint16(8, count, true); end.view.setUint16(10, count, true); end.view.setUint32(12, directoryBytes.length, true); end.view.setUint32(16, offset, true);
    return join([...entries, directoryBytes, end.bytes]);
  }
  function workbook(sheets) {
    if (!sheets.length) throw new Error('导出需要至少一个工作表');
    const ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
    const files = {
      '_rels/.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml': `<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="${ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((s, i) => `<sheet name="${xml(s.name.slice(0, 31))}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('')}</sheets></workbook>`,
      'xl/_rels/workbook.xml.rels': `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join('')}</Relationships>`,
      '[Content_Types].xml': `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`
    };
    sheets.forEach((sheet, index) => {
      const keys = [...new Set(sheet.rows.flatMap(row => Object.keys(row)))];
      const rows = [keys, ...sheet.rows.map(row => keys.map(key => row[key]))];
      if (rows.length > 1048576 || keys.length > 16384) throw new Error('数据超过 Excel 工作表容量，请分批导出');
      files[`xl/worksheets/sheet${index + 1}.xml`] = `<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="${ns}"><sheetData>${rows.map((row, ri) => `<row r="${ri + 1}">${row.map((value, ci) => typeof value === 'number' && Number.isFinite(value) ? `<c r="${col(ci)}${ri + 1}"><v>${value}</v></c>` : `<c r="${col(ci)}${ri + 1}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`).join('')}</row>`).join('')}</sheetData></worksheet>`;
    });
    return zip(files);
  }
  function download(sheets, filename) {
    const bytes = workbook(sheets), blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  global.OfflineExcel = { workbook, download };
})(typeof window === 'undefined' ? globalThis : window);
