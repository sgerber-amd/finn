import sys, re
from main import generate_compressor
from target import Target, Versal, SevenSeries
from utils.shape import Shape
from utils.mul_comp_map import MulCompMap
from typing import Optional, List

class MulCompMap_UNUSED:
	def __init__(self, na : int, nb : int, sa : bool, sb : bool):
		self.na = na
		self.nb = nb
		self.sa = sa
		self.sb = sb

	def columns(self):
		return  1 if self.na == 1 and self.nb == 1 else\
			self.nb + self.na - (not self.sb or self.sa)

	def shape(self):
		(na, nb, sa, sb) = (self.na, self.nb, self.sa, self.sb)

		res = []
		if na == 1 and nb == 1:
			res.append([7 if sa ^ sb else 8])
		else:
			col = 0

			# Crescending right triangle
			while col < nb:
				col += 1
				res.append([8] * col)
			# Central rectangle
			while col < na:
				col += 1
				res.append([8] * nb)
			# Decrescending left rectangle
			while col < nb+na-1:
				col += 1
				res.append([8] * (nb + na - col))

			# Patch in sign handling
			if sa:
				for col in range(na-1, na+nb-1):
					res[col][0] = ~res[col][0] & 15
			if sb:
				res[nb].insert(0, 2)
				for col in range(nb, nb+na-1):
					op = res[col][-1]
					res[col][-1] = ((op & 3) << 2) | ((op >> 2) & 3)
				if not sa:
					res.append([13])

		return  res

	def absolute_term(self):
		(na, nb, sa, sb) = (self.na, self.nb, self.sa, self.sb)

		return  (-1 if sa^sb else 0) if na == 1 and nb == 1 else\
			((-(sa | sb) << nb) | sa) << (na-1)

if __name__ == "__main__":

	# Parse and extract Parameters from Command Line
	sig = sys.argv[1]
	_ = re.fullmatch("(\\d+)x([us])(\\d+)([us])(\\d+)", sig).groups()
	(n, na, nb, sa, sb) = (int(_[0]), int(_[2]), int(_[4]), _[1] == 's', _[3] == 's')
	assert nb <= na

	clog2 = lambda x: (x-1).bit_length()
	np = clog2(n) + (na if nb == 1 and not sb else na+nb) if na > 1 else (
			clog2(n+1) if sa == sb else 1 + clog2(n)
		)

	map = MulCompMap(na, nb, sa, sb)
	shape = [col * n for col in map.shape()]
	print("Shape: ", ' '.join((':'.join((f"{val:x}" for val in col)) for col in shape[::-1])))

	# Absolute Term Contribution
	constants = []
	abs_term  = n * map.absolute_term()
	# Move absolute term into absorbed constant if requested
	if len(sys.argv) > 2 and sys.argv[2] == 'ca':
		print("Constant absorption.")
		if abs_term < 0:
			abs_term += 2**np
		constants = [(abs_term >> i) & 1 for i in range(np)]
		abs_term  = 0

	name = "comp_" + sig
	generate_compressor(
		target            = Versal(),
		shape             = Shape((len(col) for col in shape)),
		name              = name,
		comb_depth        = None,
		accumulate        = False,
		accumulator_width = None,
		gates = [[f"{val:x}" for val in col] for col in shape],
		constants = constants,
		path = "gen/" + name + ".sv",
		test = False
	)

	for (src, dst) in (
		("hdl/dotp_template.sv", "gen/dotp_"+sig+".sv"),
		("hdl/dotp_tb_template.sv", "gen/dotp_"+sig+"_tb.sv"),
		("hdl/dotp_template.tcl", "gen/dotp_"+sig+".tcl")
	):
		with open(src, "rt") as fsrc:
			with open(dst, "wt") as fdst:
				for l in fsrc:
					fdst.write(l
						.replace("{n}", str(n))
						.replace("{na}", str(na))
						.replace("{nb}", str(nb))
						.replace("{sa}", 's' if sa else 'u')
						.replace("{sb}", 's' if sb else 'u')
						.replace("{signed_a}", str(int(sa)))
						.replace("{signed_b}", str(int(sb)))
						.replace("{abs_term}", str(abs_term))
					)
