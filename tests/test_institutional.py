from market_data_pipeline.collectors.sec import select_document_by_root_tag
from market_data_pipeline.collectors.institutional import (
    find_latest_13f_filing,
    parse_information_table,
)

# Trimmed real fixture: Berkshire Hathaway's latest 13F-HR information table
# (accession 0001193125-25-282901), captured live from SEC EDGAR during development.
BERKSHIRE_INFO_TABLE_XML = """<informationTable xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ALLY FINL INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>02005N100</cusip>
    <value>498611260</value>
    <shrsOrPrnAmt>
      <sshPrnamt>12719675</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <otherManager>4</otherManager>
    <votingAuthority>
      <Sole>12719675</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>ALLY FINL INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>02005N100</cusip>
    <value>109911900</value>
    <shrsOrPrnAmt>
      <sshPrnamt>2803875</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <otherManager>2,4,11</otherManager>
    <votingAuthority>
      <Sole>2803875</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
</informationTable>
"""

# A non-info-table doc, standing in for a 13F's unrelated cover-page XML
# (e.g. the real primary_doc.xml, whose root is edgarSubmission, not informationTable).
COVER_PAGE_XML = '<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler"><header/></edgarSubmission>'

INFO_TABLE_TAG = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}informationTable"


def test_parse_information_table_extracts_holdings():
    df = parse_information_table(BERKSHIRE_INFO_TABLE_XML, "Berkshire Hathaway", "2025-06-30")

    assert len(df) == 2
    row = df.iloc[0]
    assert row["manager"] == "Berkshire Hathaway"
    assert row["report_date"] == "2025-06-30"
    assert row["name_of_issuer"] == "ALLY FINL INC"
    assert row["cusip"] == "02005N100"
    assert row["value"] == 498611260
    assert row["shares"] == 12719675
    assert row["share_type"] == "SH"
    assert row["investment_discretion"] == "DFND"


def test_find_latest_13f_filing_picks_first_matching_form():
    payload = {
        "filings": {
            "recent": {
                "form": ["4", "13F-HR", "13F-HR"],
                "accessionNumber": ["0001-a", "0001193125-25-282901", "0001193125-25-100000"],
                "reportDate": ["", "2025-06-30", "2025-03-31"],
                "filingDate": ["2026-01-01", "2025-08-14", "2025-05-15"],
            }
        }
    }

    filing = find_latest_13f_filing(payload)

    assert filing == {
        "accession_number": "0001193125-25-282901",
        "report_date": "2025-06-30",
        "filing_date": "2025-08-14",
    }


def test_find_latest_13f_filing_returns_none_when_no_13f():
    payload = {"filings": {"recent": {"form": ["4", "8-K"], "accessionNumber": ["a", "b"]}}}

    assert find_latest_13f_filing(payload) is None


def test_select_document_by_root_tag_finds_info_table_not_cover_page():
    candidates = {"primary_doc.xml": COVER_PAGE_XML, "46994.xml": BERKSHIRE_INFO_TABLE_XML}

    selected = select_document_by_root_tag(candidates, INFO_TABLE_TAG)

    assert selected == BERKSHIRE_INFO_TABLE_XML
