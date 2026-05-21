from pydantic import BaseModel, Field, model_validator


class Operator(BaseModel):
    operator_id: str
    name: str
    # Seconds since midnight for shift start/end (allows night shifts)
    shift_start_s: int = Field(ge=0, lt=86400)
    shift_end_s: int = Field(ge=0, lt=86400 * 2)
    certified_operations: list[str]  # OperationType values as strings
    depot_id: str

    @model_validator(mode="after")
    def shift_end_after_start(self) -> "Operator":
        if self.shift_end_s <= self.shift_start_s:
            raise ValueError("shift_end_s must be after shift_start_s")
        return self
