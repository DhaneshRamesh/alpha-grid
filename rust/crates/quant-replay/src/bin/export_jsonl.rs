//! Read-only, streaming QJ01 to JSONL bridge for offline Python research.
use quant_journal::JournalReader;
use std::io::{self, BufWriter, Write};
use std::path::Path;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .ok_or("usage: export_jsonl JOURNAL.qj")?;
    let mut reader = JournalReader::open(Path::new(&path))?;
    let mut out = BufWriter::new(io::stdout().lock());
    let mut index = 0_u64;
    while let Some(record) = reader.next_record()? {
        index += 1;
        serde_json::to_writer(
            &mut out,
            &serde_json::json!({
                "source_record_index": index,
                "record": record,
            }),
        )?;
        out.write_all(b"\n")?;
    }
    out.flush()?;
    Ok(())
}
