from market_data_pipeline.collectors.sec import select_document_by_root_tag
from market_data_pipeline.collectors.insider import parse_form4_transactions

# Trimmed real fixture: Apple Inc. Form 4 filed 2026-08-20 (accession 0001140361-26-033928),
# captured live from SEC EDGAR during development.
APPLE_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-08-18</periodOfReport>
    <issuer>
        <issuerCik>0000320193</issuerCik>
        <issuerName>Apple Inc.</issuerName>
        <issuerTradingSymbol>AAPL</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001780525</rptOwnerCik>
            <rptOwnerName>Newstead Jennifer</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>true</isOfficer>
            <officerTitle>SVP, GC and Secretary</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
            </securityTitle>
            <transactionDate>
                <value>2026-08-18</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>S</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>1439</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>307.49</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>38668</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
"""

# A non-ownership doc, standing in for a filing's unrelated cover-page XML.
OTHER_XML = "<somethingElse><foo>bar</foo></somethingElse>"


def test_parse_form4_transactions_extracts_issuer_owner_and_transaction_fields():
    df = parse_form4_transactions(APPLE_FORM4_XML, "AAPL")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "AAPL"
    assert row["issuer_trading_symbol"] == "AAPL"
    assert row["reporting_owner"] == "Newstead Jennifer"
    assert row["is_officer"] == "true"
    assert row["officer_title"] == "SVP, GC and Secretary"
    assert row["transaction_code"] == "S"
    assert row["shares"] == 1439
    assert row["price_per_share"] == 307.49
    assert row["acquired_disposed_code"] == "D"
    assert row["shares_owned_after"] == 38668


def test_parse_form4_transactions_returns_empty_when_no_nonderivative_table():
    df = parse_form4_transactions("<ownershipDocument></ownershipDocument>", "AAPL")

    assert df.empty


def test_select_document_by_root_tag_ignores_filename_picks_by_content():
    candidates = {"primary_doc.xml": OTHER_XML, "form4.xml": APPLE_FORM4_XML}

    selected = select_document_by_root_tag(candidates, "ownershipDocument")

    assert selected == APPLE_FORM4_XML


def test_select_document_by_root_tag_returns_none_when_no_match():
    candidates = {"a.xml": OTHER_XML}

    assert select_document_by_root_tag(candidates, "ownershipDocument") is None
