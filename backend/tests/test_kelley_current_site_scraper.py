from scripts.scrape_kelley_current_site import _inventory_links, _parse_page, _parse_vehicle_detail


def test_inventory_links_extracts_unique_detail_urls():
    page = _parse_page(
        """
        <html><body>
          <a href="/Inventory/Details/abc">2008 Chrysler Aspen Limited</a>
          <a href="https://www.kelleyautoplex.com/Inventory/Details/abc">duplicate</a>
          <a href="/cars-for-sale">Inventory</a>
        </body></html>
        """
    )

    assert _inventory_links(page, "https://www.kelleyautoplex.com") == [
        "https://www.kelleyautoplex.com/Inventory/Details/abc"
    ]


def test_vehicle_detail_parser_maps_core_vehicle_fields():
    html = """
    <html>
      <head><title>2008 Chrysler Aspen for sale in San Antonio, TX</title></head>
      <body>
        <h1>2008 Chrysler Aspen</h1>
        <h4>Limited SUV</h4>
        <img src="/photos/aspen-1.jpg" alt="2008 Chrysler Aspen for sale in San Antonio, TX">
        <section>
          <h2>Vehicle Info</h2>
          <div>Condition</div><div>Used</div>
          <div>Engine</div><div>5.7L V8 335hp 370ft. lbs.</div>
          <div>Drivetrain</div><div>Rear Wheel Drive</div>
          <div>Trim</div><div>Limited</div>
          <div>Fuel</div><div>Gasoline</div>
          <div>VIN</div><div>1A8HX58N08F106820</div>
          <div>Vehicle Type</div><div>SUV</div>
          <div>Ext. Color</div><div>Smoke</div>
          <div>Transmission</div><div>5-Speed Automatic</div>
        </section>
        <h2>Description</h2>
        <p>Clean local SUV.</p>
        <button>Show More</button>
        <h2>Features</h2>
        <div>Air Conditioning</div>
        <ul><li>Front air conditioning</li></ul>
        <h2>Fuel Economy</h2>
        <div>City</div><div>13</div>
        <div>Hwy</div><div>19</div>
      </body>
    </html>
    """

    vehicle = _parse_vehicle_detail(
        "https://www.kelleyautoplex.com/Inventory/Details/abc",
        html,
        "https://www.kelleyautoplex.com",
    )

    assert vehicle["title"] == "2008 Chrysler Aspen"
    assert vehicle["year"] == 2008
    assert vehicle["make"] == "Chrysler"
    assert vehicle["model"] == "Aspen"
    assert vehicle["trim"] == "Limited"
    assert vehicle["condition"] == "Used"
    assert vehicle["vin"] == "1A8HX58N08F106820"
    assert vehicle["engine"] == "5.7L V8 335hp 370ft. lbs."
    assert vehicle["transmission"] == "5-Speed Automatic"
    assert vehicle["drivetrain"] == "Rear Wheel Drive"
    assert vehicle["fuel_type"] == "Gasoline"
    assert vehicle["body_type"] == "SUV"
    assert vehicle["exterior_color"] == "Smoke"
    assert vehicle["mpg_city"] == 13
    assert vehicle["mpg_highway"] == 19
    assert vehicle["description_text"] == "Clean local SUV."
    assert vehicle["image_urls"] == [
        "https://www.kelleyautoplex.com/photos/aspen-1.jpg"
    ]
