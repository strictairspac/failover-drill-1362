from pydantic import BaseModel, Field
from src.constants.enums import VERDICTS

class CorrectionInput(BaseModel):
    verdict: str = Field(description='更正后的裁决：approved/blocked/failed')
    reason: str = Field(min_length=2, max_length=500)

    def normalized(self):
        verdict = self.verdict.strip()
        if verdict not in VERDICTS:
            raise ValueError('裁决必须是 approved / blocked / failed 之一')
        return verdict, self.reason.strip()
