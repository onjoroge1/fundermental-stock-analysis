from stock_machine.ingestion.form4 import classify, parse_form4

FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <aff10b5One>0</aff10b5One>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>5000</value></transactionShares>
        <transactionPricePerShare><value>42.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-16</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1200</value></transactionShares>
        <transactionPricePerShare><value>43.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4_extracts_and_classifies():
    rows = parse_form4(FORM4_XML)
    assert len(rows) == 2
    buy, tax = rows
    assert buy["owner"] == "DOE JANE"
    assert buy["role"] == "Chief Financial Officer"
    assert buy["classification"] == "discretionary_purchase"
    assert buy["value"] == 212500.0
    assert buy["acquired"] is True
    assert tax["classification"] == "routine_other"  # tax withholding


def test_10b5_1_plan_makes_purchase_routine():
    planned = FORM4_XML.replace("<aff10b5One>0</aff10b5One>",
                                "<aff10b5One>1</aff10b5One>")
    rows = parse_form4(planned)
    assert rows[0]["classification"] == "routine_planned"


def test_classify_codes():
    assert classify("P", False) == "discretionary_purchase"
    assert classify("S", False) == "discretionary_sale"
    assert classify("S", True) == "routine_planned"
    assert classify("M", False) == "routine_other"
    assert classify("G", False) == "routine_other"


def test_malformed_xml_yields_empty():
    assert parse_form4("<not xml") == []
