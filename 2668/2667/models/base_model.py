"""
Base model class for all 11 audit models.
"""
from abc import ABC, abstractmethod
import pandas as pd
from utils.logger import VerificationLogger


class BaseModel(ABC):
    """Abstract base for all audit models.

    Each model implements run() which:
    1. Takes data inputs (dmp DataFrame, appendix dict, region config)
    2. Applies validation logic
    3. Returns (issues_df, summary_dict)
    4. Logs all verification steps via self.logger
    """

    model_id: str = "0.0"
    model_name: str = "Base"
    priority: str = "P0"  # P0, P1, P2, P3
    dimension: str = ""

    def __init__(self, config: dict = None, output_dir: str = "output"):
        self.config = config or {}
        self.logger = VerificationLogger(self.model_id, f"{output_dir}/verification_logs")
        self.output_dir = output_dir

    @abstractmethod
    def run(
        self,
        dmp: pd.DataFrame,
        appendices: dict,
        region_auth: pd.DataFrame = None,
    ) -> tuple:
        """
        Execute the model.

        Args:
            dmp: DMP project data DataFrame
            appendices: Dict of appendix DataFrames (keys: appendix_1..appendix_5; appendix_6 is empty — data unavailable)
            region_auth: Regional authorization DataFrame

        Returns:
            (issues_df: pd.DataFrame, summary: dict)
        """
        pass

    def save_output(self, df: pd.DataFrame, filename: str = None):
        """Save model output to CSV."""
        import os
        os.makedirs(f"{self.output_dir}/model_outputs", exist_ok=True)
        if filename is None:
            filename = f"model_{self.model_id.replace('.', '_')}_output.csv"
        path = f"{self.output_dir}/model_outputs/{filename}"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  -> Saved: {path}")
        return path

    def _check_completed(self):
        """Finalize the verification log."""
        self.logger.flush()
        self.logger.print_summary()
