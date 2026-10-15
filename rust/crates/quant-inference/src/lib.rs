//! Bounded, deterministic offline adapter. No paper-policy or broker integration.
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::time::Instant;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct LinearArtifact {
    pub artifact_schema_version: u16,
    pub model_version: String,
    pub feature_schema_version: String,
    pub status: String,
    pub valid_from_ns: i64,
    pub valid_until_ns: i64,
    pub instruments: Vec<String>,
    pub horizon_ns: i64,
    pub intercept: f64,
    pub feature_names: Vec<String>,
    pub centers: Vec<f64>,
    pub scales: Vec<f64>,
    pub weights: Vec<f64>,
    pub uncertainty_nanos: Option<i64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct InferenceInput {
    pub model_version: String,
    pub feature_schema_version: String,
    pub instrument_key: String,
    pub asof_receive_ns: i64,
    pub horizon_ns: i64,
    pub features: BTreeMap<String, f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct InferenceOutput {
    pub model_version: String,
    pub status: String,
    pub score: f64,
    pub uncertainty_nanos: Option<i64>,
    pub latency_ns: u128,
}

impl LinearArtifact {
    pub fn validate(&self) -> Result<(), &'static str> {
        let n = self.feature_names.len();
        if self.artifact_schema_version != 1
            || n == 0
            || n > 6
            || self.centers.len() != n
            || self.scales.len() != n
            || self.weights.len() != n
            || self.valid_until_ns <= self.valid_from_ns
            || self.horizon_ns <= 0
            || !self.intercept.is_finite()
            || self.centers.iter().any(|x| !x.is_finite())
            || self.scales.iter().any(|x| !x.is_finite() || *x <= 0.0)
            || self.weights.iter().any(|x| !x.is_finite())
            || self.feature_names.iter().any(|x| x.is_empty())
        {
            return Err("invalid bounded model artifact");
        }
        let unique: std::collections::BTreeSet<_> = self.feature_names.iter().collect();
        if unique.len() != n {
            return Err("duplicate feature name");
        }
        Ok(())
    }

    pub fn infer(&self, input: &InferenceInput) -> Result<InferenceOutput, &'static str> {
        let start = Instant::now();
        self.validate()?;
        if self.model_version != input.model_version
            || self.feature_schema_version != input.feature_schema_version
            || self.horizon_ns != input.horizon_ns
            || !self.instruments.contains(&input.instrument_key)
            || input.asof_receive_ns < self.valid_from_ns
            || input.asof_receive_ns >= self.valid_until_ns
        {
            return Err("model version, universe or validity mismatch");
        }
        if self.status != "SHADOW_ELIGIBLE"
            && self.status != "VALIDATED_RESEARCH"
            && self.status != "TRAINED_UNVALIDATED"
        {
            return Err("unvalidated model cannot emit a prediction");
        }
        let mut score = self.intercept;
        for i in 0..self.feature_names.len() {
            let value = input
                .features
                .get(&self.feature_names[i])
                .ok_or("missing feature")?;
            if !value.is_finite() {
                return Err("nonfinite feature");
            }
            score += self.weights[i] * (value - self.centers[i]) / self.scales[i];
        }
        if !score.is_finite() {
            return Err("nonfinite prediction");
        }
        Ok(InferenceOutput {
            model_version: self.model_version.clone(),
            status: self.status.clone(),
            score,
            uncertainty_nanos: self.uncertainty_nanos,
            latency_ns: start.elapsed().as_nanos(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_fixture_and_validation() {
        let artifact: LinearArtifact =
            serde_json::from_str(include_str!("../tests/fixtures/linear.json")).unwrap();
        let input: InferenceInput =
            serde_json::from_str(include_str!("../tests/fixtures/input.json")).unwrap();
        assert_eq!(artifact.infer(&input).unwrap().score, 2.5);
        let mut missing = input.clone();
        missing.features.remove("imbalance");
        assert_eq!(artifact.infer(&missing).unwrap_err(), "missing feature");
        let mut untrained = artifact.clone();
        untrained.status = "NOT_TRAINED".into();
        assert_eq!(
            untrained.infer(&input).unwrap_err(),
            "unvalidated model cannot emit a prediction"
        );
    }
}
