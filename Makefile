.PHONY: test eval demo serve

test:
	python3 -m unittest discover -s tests -v

eval:
	python3 -m modelforge eval

demo:
	python3 -m modelforge demo

serve:
	python3 -m modelforge serve
