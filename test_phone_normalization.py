#!/usr/bin/env python3
"""
Test script for phone number normalization.
Tests the fix for the phone number corruption issue.
"""

import sys
sys.path.insert(0, '.')

from agent import _normalize_phone


def test_phone_normalization():
    """Test various phone number formats."""
    
    test_cases = [
        # (input, expected_output, description)
        ("+33 07 5808 78", "+33075808078", "French number with spaces"),
        ("+33075808078", "+33075808078", "French number without spaces"),
        ("+33-07-5808-78", "+33075808078", "French number with dashes"),
        ("+1-555-123-4567", "+15551234567", "US number with dashes"),
        ("+1 (555) 123-4567", "+15551234567", "US number with parentheses"),
        ("+44 20 7946 0958", "+442079460958", "UK number with spaces"),
        ("07 5808 78", "075808078", "Number without country code but with spaces"),
        ("", None, "Empty string"),
        (None, None, "None value"),
        ("+-566013388", None, "Corrupted format (the original bug)"),
        ("abc123def", None, "Invalid characters"),
        ("+++123", None, "Multiple plus signs"),
        ("+33 123", None, "Too short"),
        ("+33123456789012345678", None, "Too long (>15 digits)"),
        ("+33123456789", "+33123456789", "Valid E.164 format"),
    ]
    
    print("=" * 70)
    print("PHONE NUMBER NORMALIZATION TEST")
    print("=" * 70)
    
    passed = 0
    failed = 0
    
    for input_phone, expected, description in test_cases:
        result = _normalize_phone(input_phone)
        status = "✓ PASS" if result == expected else "✗ FAIL"
        
        if result == expected:
            passed += 1
        else:
            failed += 1
        
        print(f"{status} | {description}")
        print(f"    Input:    {repr(input_phone)}")
        print(f"    Expected: {repr(expected)}")
        print(f"    Got:      {repr(result)}")
        print()
    
    print("=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(test_cases)} tests")
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    success = test_phone_normalization()
    sys.exit(0 if success else 1)
