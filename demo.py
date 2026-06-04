#!/usr/bin/env python3
"""Five small algorithm demos with lightweight self-tests."""


def binary_search(items, target):
    """Return the index of target in sorted items, or -1 when missing."""
    left = 0
    right = len(items) - 1

    while left <= right:
        mid = (left + right) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            left = mid + 1
        else:
            right = mid - 1

    return -1


def bubble_sort(items):
    """Return a sorted copy of items using bubble sort."""
    result = list(items)

    for end in range(len(result) - 1, 0, -1):
        swapped = False
        for index in range(end):
            if result[index] > result[index + 1]:
                result[index], result[index + 1] = result[index + 1], result[index]
                swapped = True
        if not swapped:
            break

    return result


def factorial(number):
    """Return number! using a small recursive implementation."""
    if number < 0:
        raise ValueError("factorial is undefined for negative numbers")
    if number in (0, 1):
        return 1
    return number * factorial(number - 1)


def fibonacci(index):
    """Return the Fibonacci number at index using iteration."""
    if index < 0:
        raise ValueError("fibonacci index must be non-negative")

    previous = 0
    current = 1
    for _ in range(index):
        previous, current = current, previous + current

    return previous


def is_prime(number):
    """Return True when number is prime."""
    if number < 2:
        return False
    if number == 2:
        return True
    if number % 2 == 0:
        return False

    divisor = 3
    while divisor * divisor <= number:
        if number % divisor == 0:
            return False
        divisor += 2

    return True


def run_tests():
    assert binary_search([1, 3, 5, 7, 9], 7) == 3
    assert binary_search([1, 3, 5, 7, 9], 2) == -1
    assert bubble_sort([5, 1, 4, 2, 8]) == [1, 2, 4, 5, 8]
    assert bubble_sort([]) == []
    assert factorial(5) == 120
    assert factorial(0) == 1
    try:
        factorial(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("factorial should reject negative numbers")
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1
    assert fibonacci(7) == 13
    assert is_prime(2) is True
    assert is_prime(29) is True
    assert is_prime(1) is False
    assert is_prime(21) is False


if __name__ == "__main__":
    run_tests()
    print("All algorithm demos passed.")
