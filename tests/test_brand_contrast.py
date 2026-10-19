"""Brand colour → readable ink, and the generated landing page (stage S7c).

Run: .\\venv\\Scripts\\python.exe tests\\test_brand_contrast.py

There is no browser here, so the visual work is checked where it can be: the colour
maths the widget and the template share, and the fact that the generated page still
renders. That last one matters because INDEX_TEMPLATE is a ``str.format()`` string -
an unbalanced brace is a runtime error, not an import error, so nothing else catches it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import provisioner  # noqa: E402


def test_ink_is_readable_on_the_brand():
    # Dark brands take white; pale ones take near-black. The threshold is shared with
    # the widget's inkOn(), so the two surfaces cannot disagree.
    assert provisioner.readable_ink("#4f46e5") == "#ffffff", "indigo"
    assert provisioner.readable_ink("#0b1120") == "#ffffff", "the rail"
    assert provisioner.readable_ink("#fde68a") == "#0b1120", "pale yellow"
    assert provisioner.readable_ink("#ffffff") == "#0b1120", "white"
    assert provisioner.readable_ink("#f59e0b") == "#0b1120", "amber, near the boundary"
    print("ink: indigo and dark brands -> white, pale brands -> #0b1120")


def test_short_hex_and_rubbish():
    assert provisioner.readable_ink("#fff") == "#0b1120"
    assert provisioner._rgb("#4f4") == (0x44, 0xFF, 0x44)
    assert provisioner._rgb("not-a-colour") is None
    # A malformed colour must not raise - it falls back to white on the default brand.
    assert provisioner.readable_ink("not-a-colour") == "#ffffff"
    assert provisioner.soft_tint("nope") == "rgba(79,70,229,0.16)"
    print("bad input falls back rather than raising")


def test_the_generated_page_renders():
    html = provisioner.render_index(
        title="Acme <Tools>", tagline="We build things",
        brand_color="#fde68a", font_family="Georgia",
    )
    assert "<!DOCTYPE html>" in html
    assert "Acme &lt;Tools&gt;" in html, "title must be escaped"
    assert "--ink: #0b1120" in html, "a pale brand needs dark ink"
    assert "rgba(253,230,138,0.16)" in html, "the tint follows the hue"
    assert '"Georgia"' in html, "a safe font family is kept"
    assert "Built with" in html
    print("landing page renders, with a derived ink for a pale brand")


def test_font_injection_is_refused():
    # The font stack is interpolated into <style>, so it must not carry CSS syntax.
    evil = "x;} body{display:none} .y{"
    assert provisioner.safe_font(evil) == provisioner.SAFE_FONTS[0], evil
    assert provisioner.safe_font("") == provisioner.SAFE_FONTS[0]
    assert '"Inter"' in provisioner.safe_font("Inter")
    print("a CSS-breaking font stack falls back to the system stack")


def main():
    test_ink_is_readable_on_the_brand()
    test_short_hex_and_rubbish()
    test_the_generated_page_renders()
    test_font_injection_is_refused()
    print("\nBRAND CONTRAST OK")


if __name__ == "__main__":
    main()
