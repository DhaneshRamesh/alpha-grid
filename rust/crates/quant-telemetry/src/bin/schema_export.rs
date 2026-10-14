use quant_telemetry::protocol::{
    self, Envelope, WireCandle, WireEngineSnapshot, WireInstrumentSnapshot,
};
use std::fs;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let directory = PathBuf::from(
        std::env::args()
            .nth(1)
            .ok_or("usage: schema_export <directory>")?,
    );
    fs::create_dir_all(&directory)?;
    let schemas = [
        ("envelope.schema.json", protocol::schema_json::<Envelope>()),
        (
            "engine-snapshot.schema.json",
            protocol::schema_json::<WireEngineSnapshot>(),
        ),
        (
            "instrument-snapshot.schema.json",
            protocol::schema_json::<WireInstrumentSnapshot>(),
        ),
        ("candle.schema.json", protocol::schema_json::<WireCandle>()),
    ];
    for (name, schema) in schemas {
        fs::write(directory.join(name), serde_json::to_vec_pretty(&schema)?)?;
    }
    Ok(())
}
