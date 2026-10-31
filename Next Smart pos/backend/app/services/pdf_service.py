"""PDF generation service for invoices and financial documents."""
import os
from decimal import Decimal
from io import BytesIO
from typing import Optional

from .money import format_money

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm, cm
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
        PageBreak, KeepTogether, Flowable, Image as RLImage
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# 80mm thermal roll: the width every POS receipt printer expects.
RECEIPT_PAGE_SIZE = (80 * mm, 200 * mm)
RECEIPT_MARGIN = 5 * mm
RECEIPT_WIDTH = RECEIPT_PAGE_SIZE[0] - (2 * RECEIPT_MARGIN)

# A4 sheet for documents that get filed or emailed: quotations and tax
# invoices, as opposed to the 80mm receipt handed to the customer.
DOC_PAGE_SIZE = A4 if REPORTLAB_AVAILABLE else None
DOC_MARGIN = 15 * mm
DOC_WIDTH = (DOC_PAGE_SIZE[0] - (2 * DOC_MARGIN)) if REPORTLAB_AVAILABLE else None

SMART_NAVY = colors.HexColor('#062B5C') if REPORTLAB_AVAILABLE else None
SMART_BLUE = colors.HexColor('#087FF5') if REPORTLAB_AVAILABLE else None
SMART_GREEN = colors.HexColor('#16C784') if REPORTLAB_AVAILABLE else None
INK = colors.HexColor('#0F172A') if REPORTLAB_AVAILABLE else None
MUTED = colors.HexColor('#64748B') if REPORTLAB_AVAILABLE else None
RULE = colors.HexColor('#CBD5E1') if REPORTLAB_AVAILABLE else None


#: Human labels for the stored payment method codes.
PAYMENT_METHOD_LABELS = {
    "cash": "Cash",
    "mpesa": "M-Pesa",
    "bank": "Bank transfer",
    "card": "Card",
    "credit": "On credit",
    "other": "Other",
}


class PDFGenerationError(Exception):
    """Raised when PDF generation fails."""
    pass


class PDFService:
    """PDF generation for invoices and financial documents."""

    def __init__(self, business_id=None):
        self.business_id = business_id

    def generate_invoice_pdf(self, invoice, business, customer=None, settings=None) -> bytes:
        """Generate invoice PDF.

        Args:
            invoice: Invoice model instance
            business: Business model instance
            customer: Customer model instance (optional)
            settings: BusinessConfig model instance (optional)

        Returns:
            PDF content as bytes
        """
        if not REPORTLAB_AVAILABLE:
            raise PDFGenerationError("reportlab is not installed. Run: pip install reportlab")

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20*mm,
            leftMargin=20*mm,
            topMargin=20*mm,
            bottomMargin=20*mm,
        )

        styles = getSampleStyleSheet()
        elements = []

        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Title'],
            fontSize=24,
            spaceAfter=6,
            textColor=colors.HexColor('#1a3c5e'),
            alignment=TA_LEFT,
        )

        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#666666'),
            spaceAfter=12,
        )

        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=12,
            spaceBefore=12,
            spaceAfter=6,
            textColor=colors.HexColor('#1a3c5e'),
            borderWidth=0,
            borderPadding=0,
        )

        body_style = ParagraphStyle(
            'CustomBody',
            parent=styles['Normal'],
            fontSize=9,
            leading=12,
        )

        right_style = ParagraphStyle(
            'RightAlign',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_RIGHT,
        )

        center_style = ParagraphStyle(
            'CenterAlign',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_CENTER,
        )

        small_style = ParagraphStyle(
            'Small',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#888888'),
        )

        # ===== HEADER =====
        # Business info
        business_name = Paragraph(business.name or "Business Name", title_style)
        elements.append(business_name)

        if business.logo:
            # Logo would be added here if we had a local path
            pass

        business_details = []
        if business.default_country:
            business_details.append(f"Country: {business.default_country}")
        if business.default_currency:
            business_details.append(f"Currency: {business.default_currency}")
        if settings and settings.invoice_terms:
            business_details.append(f"Terms: {settings.invoice_terms}")

        if business_details:
            elements.append(Paragraph(" | ".join(business_details), subtitle_style))

        elements.append(Spacer(1, 10*mm))

        # Invoice title and number
        elements.append(Paragraph(f"<b>INVOICE #{invoice.invoice_number}</b>", title_style))
        elements.append(Spacer(1, 5*mm))

        # ===== INVOICE DETAILS TABLE =====
        detail_data = [
            ["Invoice Date:", invoice.date.strftime("%Y-%m-%d") if invoice.date else "N/A"],
            ["Due Date:", invoice.due_date.strftime("%Y-%m-%d") if invoice.due_date else "N/A"],
            ["Status:", self._format_status(invoice.status)],
        ]

        if invoice.branch:
            detail_data.append(["Branch:", invoice.branch.name])

        detail_table = Table(detail_data, colWidths=[40*mm, 60*mm])
        detail_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(detail_table)
        elements.append(Spacer(1, 5*mm))

        # ===== CUSTOMER INFO =====
        if customer:
            elements.append(Paragraph("<b>Bill To:</b>", heading_style))
            cust_lines = [customer.display_name]
            if customer.company_name:
                cust_lines.append(customer.company_name)
            if customer.contact_person:
                cust_lines.append(f"Attn: {customer.contact_person}")
            if customer.address:
                addr = customer.address
                addr_line = ", ".join(filter(None, [
                    addr.get("street", ""),
                    addr.get("city", ""),
                    addr.get("state", ""),
                    addr.get("postal_code", ""),
                    addr.get("country", ""),
                ]))
                if addr_line:
                    cust_lines.append(addr_line)
            if customer.email:
                cust_lines.append(f"Email: {customer.email}")
            if customer.phone:
                cust_lines.append(f"Phone: {customer.phone}")
            if customer.tax_id:
                cust_lines.append(f"Tax ID: {customer.tax_id}")

            for line in cust_lines:
                elements.append(Paragraph(line, body_style))
            elements.append(Spacer(1, 5*mm))

        # ===== LINE ITEMS =====
        elements.append(Paragraph("<b>Line Items</b>", heading_style))

        # Table header
        items_data = [
            ["#", "Description", "Qty", "Unit Price", "Discount", "Tax %", "Tax Amt", "Total"]
        ]

        for idx, item in enumerate(invoice.items, 1):
            items_data.append([
                str(idx),
                item.description,
                f"{float(item.quantity):,.2f}",
                f"{float(item.unit_price):,.2f}",
                f"{float(item.discount_amount):,.2f}" if item.discount_amount else "0.00",
                f"{float(item.tax_rate):.2f}%" if item.tax_rate else "0.00%",
                f"{float(item.tax_amount):,.2f}" if item.tax_amount else "0.00",
                f"{float(item.line_total):,.2f}",
            ])

        # Calculate column widths
        col_widths = [10*mm, 55*mm, 15*mm, 22*mm, 20*mm, 18*mm, 22*mm, 22*mm]

        items_table = Table(items_data, colWidths=col_widths, repeatRows=1)
        items_table.setStyle(TableStyle([
            # Header
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c5e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),

            # Body
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # Item number
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),  # Numbers right-aligned
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 1), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 5*mm))

        # ===== TOTALS =====
        totals_data = [
            ["Subtotal:", f"{float(invoice.subtotal):,.2f}"],
            ["Discount:", f"-{float(invoice.discount_amount):,.2f}"],
            ["Tax:", f"{float(invoice.tax_amount):,.2f}"],
        ]
        if invoice.rounding_amount and invoice.rounding_amount != 0:
            totals_data.append(["Rounding:", f"{float(invoice.rounding_amount):,.2f}"])
        totals_data.append(["<b>Total:</b>", f"<b>{float(invoice.total_amount):,.2f}</b>"])

        if invoice.paid_amount and invoice.paid_amount > 0:
            totals_data.append(["Paid:", f"{float(invoice.paid_amount):,.2f}"])
            totals_data.append(["<b>Balance Due:</b>", f"<b>{float(invoice.balance_due):,.2f}</b>"])

        totals_table = Table(totals_data, colWidths=[120*mm, 42*mm])
        totals_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -2), 'Helvetica'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor('#1a3c5e')),
        ]))
        elements.append(totals_table)
        elements.append(Spacer(1, 10*mm))

        # ===== NOTES & TERMS =====
        if invoice.notes:
            elements.append(Paragraph("<b>Notes:</b>", heading_style))
            elements.append(Paragraph(invoice.notes, body_style))
            elements.append(Spacer(1, 5*mm))

        if invoice.terms:
            elements.append(Paragraph("<b>Terms & Conditions:</b>", heading_style))
            elements.append(Paragraph(invoice.terms, small_style))
            elements.append(Spacer(1, 5*mm))

        # ===== FOOTER =====
        elements.append(Spacer(1, 10*mm))
        footer_text = "Thank you for your business!"
        if settings and settings.invoice_terms:
            footer_text = settings.invoice_terms
        elements.append(Paragraph(footer_text, ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#666666'),
        )))

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    # ------------------------------------------------------------------
    # Point-of-sale receipt
    # ------------------------------------------------------------------

    def _receipt_styles(self):
        """Compact styles sized for an 80mm roll."""
        styles = getSampleStyleSheet()

        def style(name, size, leading, **kwargs):
            return ParagraphStyle(
                name, parent=styles['Normal'], fontSize=size, leading=leading, **kwargs
            )

        return {
            "business": style('ReceiptBusiness', 13, 15, alignment=TA_CENTER,
                              textColor=SMART_NAVY),
            "contact": style('ReceiptContact', 7.5, 9.5, alignment=TA_CENTER,
                             textColor=MUTED),
            "title": style('ReceiptTitle', 10, 12, alignment=TA_CENTER,
                           textColor=INK),
            "meta": style('ReceiptMeta', 8, 10, textColor=INK),
            "meta_right": style('ReceiptMetaRight', 8, 10, alignment=TA_RIGHT,
                                textColor=INK),
            "body": style('ReceiptBody', 8, 10, textColor=INK),
            "body_right": style('ReceiptBodyRight', 8, 10, alignment=TA_RIGHT,
                                textColor=INK),
            "item": style('ReceiptItem', 8, 10, textColor=INK),
            "total": style('ReceiptTotal', 10, 12, textColor=INK),
            "total_right": style('ReceiptTotalRight', 10, 12, alignment=TA_RIGHT,
                                 textColor=SMART_NAVY),
            "muted": style('ReceiptMuted', 7.5, 9.5, alignment=TA_CENTER,
                           textColor=MUTED),
        }

    @staticmethod
    def _business_logo(business, max_width, max_height, halign="CENTER"):
        """Load the business logo from the upload folder, or None.

        The stored value is a public URL, so the on-disk path is recovered
        from the ``/uploads/`` segment. Anything unreadable is skipped rather
        than failing the whole document.
        """
        if not REPORTLAB_AVAILABLE or not business or not business.logo:
            return None
        try:
            from flask import current_app

            marker = "/uploads/"
            logo_url = str(business.logo)
            if marker not in logo_url:
                return None
            relative = logo_url.split(marker, 1)[1].split("?", 1)[0]
            if not relative or ".." in relative:
                return None
            path = os.path.join(current_app.config["UPLOAD_FOLDER"], relative)
            if not os.path.isfile(path):
                return None
            image = RLImage(path)
            image.drawWidth = min(image.drawWidth, max_width)
            if image.drawHeight > max_height:
                scale = max_height / image.drawHeight
                image.drawWidth *= scale
                image.drawHeight = max_height
            image.hAlign = halign
            return image
        except Exception:
            return None

    @staticmethod
    def _receipt_logo(business):
        """Receipt-sized logo, centred and capped to the roll width."""
        return PDFService._business_logo(business, RECEIPT_WIDTH, 18 * mm, "CENTER")

    @staticmethod
    def _doc_logo(business):
        """A4-sized logo, left aligned and capped so it cannot dominate."""
        return PDFService._business_logo(business, 45 * mm, 22 * mm, "LEFT")

    @staticmethod
    def _receipt_contact_lines(business):
        """Postal address and contact details, in the order a receipt reads best."""
        lines = []
        for value in (business.address_line1, business.address_line2):
            if value:
                lines.append(str(value))
        locality = ", ".join(
            part for part in (business.city, business.state, business.postal_code) if part
        )
        if locality:
            lines.append(locality)
        if business.default_country:
            lines.append(str(business.default_country))
        contacts = []
        if business.phone:
            contacts.append(f"Tel: {business.phone}")
        if business.email:
            contacts.append(f"Email: {business.email}")
        if business.website:
            contacts.append(str(business.website))
        if contacts:
            lines.append(" | ".join(contacts))
        return lines

    def _receipt_meta_rows(self, sale, served_by=""):
        """Two-column reference block: label on the left, value on the right."""
        sold_at = sale.date
        if sold_at is not None and getattr(sold_at, "tzinfo", None) is not None:
            sold_at = sold_at.astimezone().replace(tzinfo=None)
        rows = [
            ("Receipt No.", sale.invoice_number or ""),
            ("Date", sold_at.strftime("%Y-%m-%d %H:%M") if sold_at else ""),
        ]
        customer_name = None
        if sale.customer is not None:
            customer_name = sale.customer.display_name
        rows.append(("Customer", customer_name or "Walk-in customer"))
        if sale.branch is not None and sale.branch.name:
            rows.append(("Branch", sale.branch.name))
        if served_by:
            rows.append(("Served By", served_by))
        return [(label, str(value)) for label, value in rows if value]

    def generate_receipt_pdf(self, sale, business, *, served_by="", tendered=None,
                             change_due=None, settings=None) -> bytes:
        """Generate an 80mm point-of-sale receipt for a completed sale.

        Args:
            sale: SaleInvoice model instance.
            business: Business model instance.
            served_by: Display name of the cashier, shown on the receipt.
            tendered: Cash handed over by the customer, for the change line.
            change_due: Change returned to the customer.
            settings: BusinessConfig model instance (optional).

        Returns:
            PDF content as bytes.
        """
        if not REPORTLAB_AVAILABLE:
            raise PDFGenerationError("reportlab is not installed. Run: pip install reportlab")

        currency = getattr(business, "default_currency", None)
        styles = self._receipt_styles()
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=RECEIPT_PAGE_SIZE,
            leftMargin=RECEIPT_MARGIN,
            rightMargin=RECEIPT_MARGIN,
            topMargin=RECEIPT_MARGIN,
            bottomMargin=RECEIPT_MARGIN,
            title=f"Receipt {sale.invoice_number or ''}".strip(),
            author=business.name or "SmartBiz",
        )

        elements = []

        logo = self._receipt_logo(business)
        if logo is not None:
            elements.append(logo)
            elements.append(Spacer(1, 1.5 * mm))

        elements.append(Paragraph(self._escape(business.name or "Business"), styles["business"]))
        for line in self._receipt_contact_lines(business):
            elements.append(Paragraph(self._escape(line), styles["contact"]))

        elements.append(Spacer(1, 2 * mm))
        elements.append(self._receipt_rule())
        elements.append(Paragraph("SALES RECEIPT", styles["title"]))
        elements.append(self._receipt_rule())
        elements.append(Spacer(1, 1 * mm))

        meta_rows = self._receipt_meta_rows(sale, served_by)
        meta_data = [
            [Paragraph(self._escape(label), styles["meta"]),
             Paragraph(self._escape(value), styles["meta_right"])]
            for label, value in meta_rows
        ]
        meta_table = Table(meta_data, colWidths=[RECEIPT_WIDTH * 0.42, RECEIPT_WIDTH * 0.58])
        meta_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 0.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.5),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 1.5 * mm))
        elements.append(self._receipt_rule())

        # Line items: description on the first line, qty x price below it, so
        # long product names are not truncated on a narrow roll.
        item_rows = [[
            Paragraph("<b>Item</b>", styles["item"]),
            Paragraph("<b>Total</b>", styles["body_right"]),
        ]]
        for line in sale.items:
            item_rows.append([
                Paragraph(
                    f"{self._escape(line.description)}"
                    f"<br/><font size=7 color='#64748B'>{self._escape(self._line_detail(line))}</font>",
                    styles["item"],
                ),
                Paragraph(format_money(line.line_total, currency), styles["body_right"]),
            ])
        items_table = Table(item_rows, colWidths=[RECEIPT_WIDTH * 0.68, RECEIPT_WIDTH * 0.32])
        items_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('LINEBELOW', (0, 0), (-1, -2), 0.25, RULE),
        ]))
        elements.append(items_table)

        elements.append(Spacer(1, 1.5 * mm))
        elements.append(self._receipt_rule())

        total_rows = [
            ("Subtotal", format_money(sale.subtotal, currency), "body", "body_right"),
        ]
        if sale.discount_amount:
            total_rows.append(("Discount", f"-{format_money(sale.discount_amount, currency)}", "body", "body_right"))
        if sale.tax_amount:
            total_rows.append(("Tax", format_money(sale.tax_amount, currency), "body", "body_right"))
        if sale.rounding_amount:
            total_rows.append(("Rounding", format_money(sale.rounding_amount, currency), "body", "body_right"))
        total_rows.append(("TOTAL", format_money(sale.total_amount, currency), "total", "total_right"))

        rendered = [[
            Paragraph(label, styles[label_style]),
            Paragraph(value, styles[value_style]),
        ] for label, value, label_style, value_style in total_rows]
        totals_table = Table(rendered, colWidths=[RECEIPT_WIDTH * 0.5, RECEIPT_WIDTH * 0.5])
        totals_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('LINEABOVE', (0, -1), (-1, -1), 0.75, SMART_NAVY),
        ]))
        elements.append(totals_table)

        for payment in (sale.payments or []):
            elements.append(Paragraph(
                f"Paid by {self._escape(self._payment_method_label(payment.payment_method))}: "
                f"{self._escape(format_money(payment.amount, currency))}",
                styles["body"],
            ))
        if tendered is not None:
            elements.append(Paragraph(
                f"Cash tendered: {self._escape(format_money(tendered, currency))}",
                styles["body"],
            ))
        if change_due is not None and change_due:
            elements.append(Paragraph(
                f"Change due: {self._escape(format_money(change_due, currency))}",
                styles["body"],
            ))
        if sale.balance_due and sale.balance_due > 0:
            elements.append(Paragraph(
                f"<b>Balance due: {self._escape(format_money(sale.balance_due, currency))}</b>",
                styles["body"],
            ))

        elements.append(Spacer(1, 1.5 * mm))
        elements.append(self._receipt_rule())

        if sale.notes:
            elements.append(Paragraph("<b>Notes</b>", styles["meta"]))
            elements.append(Paragraph(self._escape(sale.notes), styles["body"]))
            elements.append(Spacer(1, 1 * mm))

        footer = "Thank you for your business!"
        if settings is not None and settings.invoice_terms:
            footer = settings.invoice_terms
        elements.append(Paragraph(self._escape(footer), styles["muted"]))
        elements.append(Spacer(1, 1 * mm))
        elements.append(Paragraph("Powered by SmartBiz POS", styles["muted"]))

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    @staticmethod
    def _payment_method_label(method: str) -> str:
        return PAYMENT_METHOD_LABELS.get(str(method or "").lower(), str(method or "").title())

    @staticmethod
    def _line_detail(line) -> str:
        """Second line of a receipt item: quantity, unit price, tax.

        The unit price is shown without a currency symbol because the receipt
        already carries one on every total.
        """
        quantity = f"{float(line.quantity or 0):g}"
        unit_price = f"{float(line.unit_price or 0):,.2f}"
        detail = f"{quantity} x {unit_price}"
        if line.discount_amount:
            detail += f" | Discount {float(line.discount_amount):,.2f}"
        if line.tax_amount:
            detail += f" | Tax {float(line.tax_amount):,.2f}"
        return detail

    @staticmethod
    def _receipt_rule():
        """A hairline separator spanning the printable width."""
        rule = Table([[""]], colWidths=[RECEIPT_WIDTH], rowHeights=[0.5])
        rule.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 0.5, RULE),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return rule

    @staticmethod
    def _escape(value) -> str:
        """Escape text for reportlab's mini-HTML paragraph markup."""
        text = "" if value is None else str(value)
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    # --- A4 document building blocks (quotations, tax invoices) -------------

    def _doc_styles(self):
        """Styles for an A4 sheet, sharing the receipt's SmartBiz palette."""
        styles = getSampleStyleSheet()

        def style(name, size, leading, **kwargs):
            return ParagraphStyle(
                name, parent=styles['Normal'], fontSize=size, leading=leading, **kwargs
            )

        return {
            "business": style('DocBusiness', 15, 18, textColor=SMART_NAVY),
            "contact": style('DocContact', 8, 11, textColor=MUTED),
            "title": style('DocTitle', 13, 16, textColor=SMART_NAVY),
            "heading": style('DocHeading', 8, 10, textColor=MUTED),
            "meta": style('DocMeta', 9, 12, textColor=INK),
            "meta_label": style('DocMetaLabel', 8, 10, textColor=MUTED),
            "meta_right": style('DocMetaRight', 9, 12, alignment=TA_RIGHT, textColor=INK),
            "body": style('DocBody', 9, 12, textColor=INK),
            "body_right": style('DocBodyRight', 9, 12, alignment=TA_RIGHT, textColor=INK),
            "total": style('DocTotal', 10, 13, textColor=INK),
            "total_right": style('DocTotalRight', 10, 13, alignment=TA_RIGHT, textColor=SMART_NAVY),
            "muted": style('DocMuted', 8, 11, textColor=MUTED),
        }

    @staticmethod
    def _doc_rule(width=None):
        """A hairline separator spanning an A4's printable width."""
        rule = Table([[""]], colWidths=[width or DOC_WIDTH], rowHeights=[0.5])
        rule.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 0.5, RULE),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return rule

    def _doc_meta_table(self, rows, styles, width=None):
        """A label/value block, label left and value right-aligned."""
        total_width = width or DOC_WIDTH
        rendered = [
            [
                Paragraph(self._escape(label), styles["meta_label"]),
                Paragraph(self._escape(value), styles["meta_right"]),
            ]
            for label, value in rows
            if value
        ]
        table = Table(rendered, colWidths=[total_width * 0.45, total_width * 0.55])
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        return table

    def _doc_items_table(self, items, currency, styles, width=None):
        """Line items: description, qty, unit price, tax and line total."""
        total_width = width or DOC_WIDTH
        columns = [0.46, 0.10, 0.16, 0.12, 0.16]
        header = ["Description", "Qty", "Unit price", "Tax", "Amount"]
        rows = [[Paragraph(f"<b>{self._escape(h)}</b>", styles["body_right"] if i else styles["body"])
                 for i, h in enumerate(header)]]
        for line in items or []:
            rows.append([
                Paragraph(self._escape(line.description or ""), styles["body"]),
                Paragraph(f"{float(line.quantity or 0):g}", styles["body_right"]),
                Paragraph(format_money(line.unit_price, currency), styles["body_right"]),
                Paragraph(format_money(line.tax_amount, currency) if line.tax_amount else "-",
                          styles["body_right"]),
                Paragraph(format_money(line.line_total, currency), styles["body_right"]),
            ])
        table = Table(
            rows,
            colWidths=[total_width * f for f in columns],
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('LINEBELOW', (0, 0), (-1, -2), 0.25, RULE),
            ('LINEBELOW', (0, 0), (-1, 0), 0.75, SMART_NAVY),
        ]))
        return table

    def _doc_totals_table(self, total_rows, styles, width=None):
        """Totals block, indented to the right of the line items."""
        total_width = width or DOC_WIDTH
        rendered = [
            [
                Paragraph(self._escape(label), styles[label_style]),
                Paragraph(self._escape(value), styles[value_style]),
            ]
            for label, value, label_style, value_style in total_rows
        ]
        table = Table(rendered, colWidths=[total_width * 0.26, total_width * 0.24])
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('LINEABOVE', (0, 0), (-1, 0), 0.5, RULE),
            ('LINEABOVE', (0, -1), (-1, -1), 0.75, SMART_NAVY),
        ]))
        # Right-align the block against the page edge.
        table.hAlign = "RIGHT"
        return table

    @staticmethod
    def _doc_date(value):
        """A stored datetime rendered without its offset, or a date as-is."""
        if value is None:
            return ""
        if getattr(value, "tzinfo", None) is not None:
            value = value.astimezone().replace(tzinfo=None)
        try:
            return value.strftime("%Y-%m-%d")
        except AttributeError:
            return str(value)

    @staticmethod
    def _doc_timestamp(value):
        if value is None:
            return ""
        if getattr(value, "tzinfo", None) is not None:
            value = value.astimezone().replace(tzinfo=None)
        try:
            return value.strftime("%Y-%m-%d %H:%M")
        except AttributeError:
            return str(value)

    def _doc_footer(self, settings):
        """Terms block, falling back to a neutral thank-you."""
        return settings.invoice_terms if settings is not None and settings.invoice_terms else ""

    def generate_quotation_pdf(self, quotation, business, *, customer=None, settings=None) -> bytes:
        """Generate an A4 quotation for a customer who has not paid yet.

        Args:
            quotation: Quotation model instance, with ``items`` loaded.
            business: Business model instance.
            customer: Customer model instance (optional; defaults to the
                quotation's own relationship).
            settings: BusinessConfig model instance (optional).

        Returns:
            PDF content as bytes.
        """
        if not REPORTLAB_AVAILABLE:
            raise PDFGenerationError("reportlab is not installed. Run: pip install reportlab")

        currency = getattr(business, "default_currency", None)
        styles = self._doc_styles()
        buffer = BytesIO()

        customer = customer if customer is not None else quotation.customer
        meta_rows = [
            ("Quotation No.", quotation.quotation_number or ""),
            ("Date", self._doc_date(quotation.date)),
            ("Valid until", self._doc_date(quotation.expiry_date)),
            ("Status", self._format_status(quotation.status)),
        ]
        if quotation.branch is not None and quotation.branch.name:
            meta_rows.append(("Branch", quotation.branch.name))

        elements = [self._doc_header(business, meta_rows, styles)]
        elements.append(Paragraph("QUOTATION", styles["title"]))
        elements.append(Spacer(1, 4 * mm))
        elements.extend(self._doc_party_block(customer, "Prepared for", styles))
        elements.append(Spacer(1, 3 * mm))

        elements.append(self._doc_items_table(quotation.items, currency, styles))

        total_rows = self._doc_base_total_rows(quotation, currency)
        total_rows.append(("TOTAL", format_money(quotation.total_amount, currency),
                           "total", "total_right"))
        elements.append(Spacer(1, 2 * mm))
        elements.append(self._doc_totals_table(total_rows, styles))

        if quotation.notes:
            elements.append(Spacer(1, 4 * mm))
            elements.append(Paragraph("<b>Notes</b>", styles["heading"]))
            elements.append(Paragraph(self._escape(quotation.notes), styles["body"]))

        return self._doc_finish(
            buffer, elements,
            title=f"Quotation {quotation.quotation_number or ''}".strip(),
            business=business, styles=styles, settings=settings,
        )

    def _doc_business_block(self, business, styles):
        """Logo, business name and contact lines, as one flowable cell."""
        flowables = []
        logo = self._doc_logo(business)
        if logo is not None:
            flowables.append(logo)
        flowables.append(Paragraph(self._escape(business.name or "Business"), styles["business"]))
        for line in self._receipt_contact_lines(business):
            flowables.append(Paragraph(self._escape(line), styles["contact"]))
        return flowables

    def _doc_header(self, business, meta_rows, styles):
        """Two-up header: who issued the document, and what it refers to."""
        header = Table(
            [
                [self._doc_business_block(business, styles),
                 self._doc_meta_table(meta_rows, styles, width=DOC_WIDTH * 0.45)]
            ],
            colWidths=[DOC_WIDTH * 0.55, DOC_WIDTH * 0.45],
        )
        header.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return header

    def _doc_party_block(self, customer, heading, styles, walk_in_label="Walk-in customer"):
        """The addressee block under the document title."""
        block = [Paragraph(heading, styles["heading"])]
        if customer is None:
            block.append(Paragraph(self._escape(walk_in_label), styles["body"]))
            return block
        block.append(Paragraph(f"<b>{self._escape(customer.display_name)}</b>", styles["body"]))
        for detail in (customer.phone, customer.email, customer.tax_id):
            if detail:
                block.append(Paragraph(self._escape(detail), styles["muted"]))
        return block

    def _doc_finish(self, buffer, elements, *, title, business, styles, settings):
        """Append terms and the footer rule, then render and return the bytes."""
        terms = self._doc_footer(settings)
        if terms:
            elements.append(Spacer(1, 6 * mm))
            elements.append(Paragraph(self._escape(terms), styles["muted"]))

        doc = SimpleDocTemplate(
            buffer,
            pagesize=DOC_PAGE_SIZE,
            leftMargin=DOC_MARGIN,
            rightMargin=DOC_MARGIN,
            topMargin=DOC_MARGIN,
            bottomMargin=DOC_MARGIN,
            title=title,
            author=business.name or "SmartBiz",
        )
        elements.append(self._doc_rule())
        elements.append(Spacer(1, 1.5 * mm))
        elements.append(Paragraph("Powered by SmartBiz POS", styles["muted"]))
        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    def _doc_base_total_rows(self, document, currency):
        """Subtotal, discount, tax and rounding, shared by both documents."""
        rows = [("Subtotal", format_money(document.subtotal, currency), "body", "body_right")]
        if document.discount_amount:
            rows.append(("Discount", f"-{format_money(document.discount_amount, currency)}",
                         "body", "body_right"))
        if document.tax_amount:
            rows.append(("Tax", format_money(document.tax_amount, currency), "body", "body_right"))
        if getattr(document, "rounding_amount", None):
            rows.append(("Rounding", format_money(document.rounding_amount, currency),
                         "body", "body_right"))
        return rows

    def generate_sale_invoice_pdf(self, sale, business, *, served_by="", settings=None) -> bytes:
        """Generate an A4 tax invoice for a completed sale.

        Distinct from the 80mm receipt: this is the filed record, so it shows
        the full line-item table, every payment, and the outstanding balance.

        Args:
            sale: SaleInvoice model instance, with ``items``/``payments`` loaded.
            business: Business model instance.
            served_by: Display name of the cashier, shown on the invoice.
            settings: BusinessConfig model instance (optional).

        Returns:
            PDF content as bytes.
        """
        if not REPORTLAB_AVAILABLE:
            raise PDFGenerationError("reportlab is not installed. Run: pip install reportlab")

        currency = getattr(business, "default_currency", None)
        styles = self._doc_styles()
        buffer = BytesIO()

        customer = sale.customer
        meta_rows = [
            ("Invoice No.", sale.invoice_number or ""),
            ("Date", self._doc_timestamp(sale.date)),
            ("Due date", self._doc_date(sale.due_date)),
            ("Status", self._format_status(sale.status)),
        ]
        if sale.branch is not None and sale.branch.name:
            meta_rows.append(("Branch", sale.branch.name))
        if served_by:
            meta_rows.append(("Served by", served_by))

        elements = [self._doc_header(business, meta_rows, styles)]
        elements.append(Paragraph("TAX INVOICE", styles["title"]))
        elements.append(Spacer(1, 4 * mm))
        elements.extend(self._doc_party_block(customer, "Billed to", styles))
        elements.append(Spacer(1, 3 * mm))

        elements.append(self._doc_items_table(sale.items, currency, styles))

        total_rows = self._doc_base_total_rows(sale, currency)
        total_rows.append(("TOTAL", format_money(sale.total_amount, currency),
                           "total", "total_right"))
        if sale.paid_amount:
            total_rows.append(("Paid", f"-{format_money(sale.paid_amount, currency)}",
                               "body", "body_right"))
        total_rows.append(("BALANCE DUE", format_money(sale.balance_due or 0, currency),
                           "total", "total_right"))

        elements.append(Spacer(1, 2 * mm))
        elements.append(self._doc_totals_table(total_rows, styles))

        for payment in (sale.payments or []):
            reference = f" · {payment.reference}" if payment.reference else ""
            elements.append(Spacer(1, 1.5 * mm))
            elements.append(Paragraph(
                f"Paid by {self._escape(self._payment_method_label(payment.payment_method))}"
                f"{self._escape(reference)}: "
                f"{self._escape(format_money(payment.amount, currency))}",
                styles["body"],
            ))

        if sale.notes:
            elements.append(Spacer(1, 4 * mm))
            elements.append(Paragraph("<b>Notes</b>", styles["heading"]))
            elements.append(Paragraph(self._escape(sale.notes), styles["body"]))

        return self._doc_finish(
            buffer, elements,
            title=f"Invoice {sale.invoice_number or ''}".strip(),
            business=business, styles=styles, settings=settings,
        )

    def _format_status(self, status: str) -> str:
        """Format status for display."""
        status_map = {
            "draft": "Draft",
            "sent": "Sent",
            "partially_paid": "Partially Paid",
            "paid": "Paid",
            "overdue": "Overdue",
            "cancelled": "Cancelled",
        }
        return status_map.get(status, status.title())

    def generate_statement_pdf(self, customer, invoices, payments, business) -> bytes:
        """Generate customer statement PDF."""
        return self.generate_party_statement_pdf(
            customer, invoices, payments, business,
            party_label="Customer", document_label="Invoice",
            reference_attr="invoice_number",
        )

    def generate_party_statement_pdf(self, party, documents, payments, business, *,
                                     party_label="Customer", document_label="Invoice",
                                     reference_attr="invoice_number",
                                     amount_attrs=("total_amount", "amount"),
                                     date_attr="date") -> bytes:
        """Generate a statement of account for any business counterparty.

        Customers are billed from their invoices, suppliers are credited from
        their expenses; the only differences are the labels and the attribute
        names carrying the reference number, date and amount.
        """
        if not REPORTLAB_AVAILABLE:
            raise PDFGenerationError("reportlab is not installed")

        document_amount_attr, payment_amount_attr = amount_attrs

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)

        styles = getSampleStyleSheet()
        elements = []

        title_style = ParagraphStyle('StmtTitle', parent=styles['Title'], fontSize=20, spaceAfter=6, textColor=colors.HexColor('#1a3c5e'))
        heading_style = ParagraphStyle('StmtHeading', parent=styles['Heading2'], fontSize=12, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor('#1a3c5e'))
        body_style = ParagraphStyle('StmtBody', parent=styles['Normal'], fontSize=9, leading=12)

        elements.append(Paragraph("STATEMENT OF ACCOUNT", title_style))
        elements.append(Paragraph(business.name or "Business Name", body_style))
        elements.append(Spacer(1, 5*mm))

        # Counterparty info
        elements.append(Paragraph(f"<b>{party_label}:</b>", heading_style))
        party_lines = [party.display_name]
        if party.company_name:
            party_lines.append(party.company_name)
        if party.email:
            party_lines.append(f"Email: {party.email}")
        if party.phone:
            party_lines.append(f"Phone: {party.phone}")
        for line in party_lines:
            elements.append(Paragraph(str(line), body_style))
        elements.append(Spacer(1, 5*mm))

        # Statement table
        stmt_data = [["Date", "Type", "Reference", "Debit", "Credit", "Balance"]]

        balance = Decimal("0")
        for document in documents:
            amount = Decimal(str(getattr(document, document_amount_attr, 0) or 0))
            balance += amount
            occurred = getattr(document, date_attr, None)
            stmt_data.append([
                occurred.strftime("%Y-%m-%d") if occurred else "",
                document_label,
                str(getattr(document, reference_attr, "") or ""),
                f"{float(amount):,.2f}",
                "",
                f"{float(balance):,.2f}",
            ])

        for payment in payments or []:
            amount = Decimal(str(getattr(payment, payment_amount_attr, 0) or 0))
            balance -= amount
            occurred = getattr(payment, date_attr, None)
            stmt_data.append([
                occurred.strftime("%Y-%m-%d") if occurred else "",
                "Payment",
                str(getattr(payment, "payment_number", "") or ""),
                "",
                f"{float(amount):,.2f}",
                f"{float(balance):,.2f}",
            ])

        stmt_table = Table(stmt_data, colWidths=[25*mm, 25*mm, 35*mm, 30*mm, 30*mm, 30*mm], repeatRows=1)
        stmt_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c5e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
        ]))
        elements.append(stmt_table)
        elements.append(Spacer(1, 10*mm))

        # Final balance
        elements.append(Paragraph(f"<b>Outstanding Balance: {float(balance):,.2f}</b>", heading_style))

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes


    def generate_report_pdf(self, title: str, rows: list[dict]) -> bytes:
        """Generate a generic tabular PDF report from a list of dict rows."""
        if not REPORTLAB_AVAILABLE:
            raise PDFGenerationError("reportlab is not installed. Run: pip install reportlab")

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=15*mm, leftMargin=15*mm, topMargin=20*mm, bottomMargin=15*mm,
        )
        styles = getSampleStyleSheet()
        elements = []

        title_style = ParagraphStyle(
            'ReportTitle', parent=styles['Title'], fontSize=16,
            spaceAfter=12, textColor=colors.HexColor('#1a3c5e'), alignment=TA_CENTER,
        )
        body_style = ParagraphStyle('ReportBody', parent=styles['Normal'], fontSize=8, leading=10)

        elements.append(Paragraph(title, title_style))

        if not rows:
            elements.append(Paragraph("No data available for this report.", body_style))
            doc.build(elements)
            return buffer.getvalue()

        headers = list(rows[0].keys())
        table_data = [headers]
        for row in rows:
            table_data.append([_format_cell(row.get(h)) for h in headers])

        col_widths = [min(50*mm, max(20*mm, len(str(h)) * 1.2 * mm)) for h in headers]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c5e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f6f8')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(table)

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes


def _format_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, Decimal):
        return f"{float(value):,.2f}"
    return str(value)


def generate_report_pdf(title: str, rows: list[dict]) -> bytes:
    """Module-level convenience wrapping PDFService.generate_report_pdf."""
    return get_pdf_service().generate_report_pdf(title, rows)


def get_pdf_service(business_id=None):
    return PDFService(business_id)