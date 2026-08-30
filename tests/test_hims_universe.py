from stock_machine.sectors import UNIVERSE, classify


def test_hims_is_in_coverage_universe():
    assert "HIMS" in UNIVERSE


def test_hims_sec_sic_maps_to_healthcare_services():
    # SEC CIK 0001773751 reports SIC 8011: Offices & Clinics of Doctors.
    assert classify("8011") == "Healthcare Services"
