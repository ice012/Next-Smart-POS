"""Tax configuration and calculation service."""
from decimal import Decimal
from typing import Optional

from sqlalchemy import select

from ..exceptions import NotFoundError, ConflictError, ValidationError
from ..extensions import db
from ..models import TaxConfig
from ..utils.db import commit
from ..utils.security import to_uuid


class TaxService:
    """Decoupled tax configuration and calculation service."""

    def __init__(self, business_id):
        self.business_id = business_id

    def list_tax_configs(self, is_active: bool = True):
        stmt = select(TaxConfig).where(TaxConfig.business_id == self.business_id)
        if is_active is not None:
            stmt = stmt.where(TaxConfig.is_active.is_(is_active))
        stmt = stmt.order_by(TaxConfig.is_default.desc(), TaxConfig.name.asc())
        return db.session.scalars(stmt).all()

    def get_tax_config(self, tax_id: str):
        tax = db.session.get(TaxConfig, tax_id)
        if tax is None or tax.business_id != self.business_id:
            raise NotFoundError("Tax configuration not found")
        return tax

    def get_default_tax(self, applies_to: str = "sales") -> Optional[TaxConfig]:
        """Get default tax for sales or purchases."""
        stmt = select(TaxConfig).where(
            TaxConfig.business_id == self.business_id,
            TaxConfig.is_default.is_(True),
            TaxConfig.is_active.is_(True),
            TaxConfig.applies_to.in_([applies_to, "both"]),
        )
        return db.session.scalar(stmt)

    def create_tax_config(self, data, actor_id):
        # Check unique code
        existing = db.session.scalar(
            select(TaxConfig).where(
                TaxConfig.business_id == self.business_id,
                TaxConfig.code == data.code,
            )
        )
        if existing:
            raise ConflictError("A tax configuration with this code already exists")

        # Check unique name
        existing = db.session.scalar(
            select(TaxConfig).where(
                TaxConfig.business_id == self.business_id,
                TaxConfig.name == data.name,
            )
        )
        if existing:
            raise ConflictError("A tax configuration with this name already exists")

        # If setting as default, unset other defaults for same applies_to
        if data.is_default:
            db.session.execute(
                TaxConfig.__table__.update()
                .where(
                    TaxConfig.business_id == self.business_id,
                    TaxConfig.applies_to.in_([data.applies_to, "both"]),
                    TaxConfig.is_default.is_(True),
                )
                .values(is_default=False)
            )

        tax = TaxConfig(
            business_id=self.business_id,
            name=data.name,
            code=data.code,
            rate=Decimal(str(data.rate)),
            is_inclusive=data.is_inclusive,
            is_default=data.is_default,
            applies_to=data.applies_to,
            description=data.description,
        )
        tax.created_by = actor_id
        tax.updated_by = actor_id
        db.session.add(tax)
        commit()
        return tax

    def update_tax_config(self, tax_id: str, data, actor_id):
        tax = self.get_tax_config(tax_id)

        if data.name is not None and data.name != tax.name:
            existing = db.session.scalar(
                select(TaxConfig).where(
                    TaxConfig.business_id == self.business_id,
                    TaxConfig.name == data.name,
                    TaxConfig.id != tax_id,
                )
            )
            if existing:
                raise ConflictError("A tax configuration with this name already exists")
            tax.name = data.name

        if data.code is not None and data.code != tax.code:
            existing = db.session.scalar(
                select(TaxConfig).where(
                    TaxConfig.business_id == self.business_id,
                    TaxConfig.code == data.code,
                    TaxConfig.id != tax_id,
                )
            )
            if existing:
                raise ConflictError("A tax configuration with this code already exists")
            tax.code = data.code

        if data.rate is not None:
            tax.rate = Decimal(str(data.rate))

        if data.is_inclusive is not None:
            tax.is_inclusive = data.is_inclusive

        if data.is_default is not None and data.is_default != tax.is_default:
            if data.is_default:
                db.session.execute(
                    TaxConfig.__table__.update()
                    .where(
                        TaxConfig.business_id == self.business_id,
                        TaxConfig.applies_to.in_([tax.applies_to, "both"]),
                        TaxConfig.is_default.is_(True),
                    )
                    .values(is_default=False)
                )
            tax.is_default = data.is_default

        if data.applies_to is not None:
            tax.applies_to = data.applies_to

        if data.description is not None:
            tax.description = data.description

        if data.is_active is not None:
            tax.is_active = data.is_active

        tax.updated_by = actor_id
        commit()
        return tax

    def delete_tax_config(self, tax_id: str):
        tax = self.get_tax_config(tax_id)
        if tax.is_default:
            raise ValidationError("Cannot delete default tax configuration. Set another as default first.")
        db.session.delete(tax)
        commit()

    def calculate_tax(
        self,
        amount: Decimal,
        tax_rate: Optional[Decimal] = None,
        tax_code: Optional[str] = None,
        is_inclusive: bool = False
    ) -> dict:
        """Calculate tax amount from base amount."""
        if tax_rate is None and tax_code:
            tax = db.session.scalar(
                select(TaxConfig).where(
                    TaxConfig.business_id == self.business_id,
                    TaxConfig.code == tax_code,
                    TaxConfig.is_active.is_(True),
                )
            )
            if tax:
                tax_rate = tax.rate
                is_inclusive = tax.is_inclusive

        if tax_rate is None:
            tax_rate = Decimal("0")

        if is_inclusive:
            # Extract tax from inclusive amount
            if tax_rate == 0:
                return {"base_amount": amount, "tax_amount": Decimal("0"), "total_amount": amount}
            divisor = Decimal("1") + (tax_rate / Decimal("100"))
            base = amount / divisor
            tax_amount = amount - base
            return {
                "base_amount": base.quantize(Decimal("0.01")),
                "tax_amount": tax_amount.quantize(Decimal("0.01")),
                "total_amount": amount,
            }
        else:
            # Add tax to base amount
            tax_amount = amount * (tax_rate / Decimal("100"))
            total = amount + tax_amount
            return {
                "base_amount": amount,
                "tax_amount": tax_amount.quantize(Decimal("0.01")),
                "total_amount": total.quantize(Decimal("0.01")),
            }


def get_tax_service(business_id):
    return TaxService(business_id)