# Generated from Branch Builder
# Each two-node branch stamps [[G, -G], [-G, G]] and [Ihis, -Ihis].
import sympy as sp

# Symbol declarations inferred from the current circuit
AA, AN, AP, BB, BN, BP, CC, CN, CP, G_rc, G11, G12, G22, gC, IAhis_rc, IBhis_rc, IC_his0, IChis_rc, Ihis_a, Ihis_A, Ihis_b, Ihis_B, Ihis_c, Ihis_C, Ihis_exclude_c0, Ihis_exclude_c1, Ihis_exclude_c2, Ihis_exclude_c3, Ihis_exclude_c4, NN, PN, PP, w1, w2 = sp.symbols("AA AN AP BB BN BP CC CN CP G_rc G11 G12 G22 gC IAhis_rc IBhis_rc IC_his0 IChis_rc Ihis_a Ihis_A Ihis_b Ihis_B Ihis_c Ihis_C Ihis_exclude_c0 Ihis_exclude_c1 Ihis_exclude_c2 Ihis_exclude_c3 Ihis_exclude_c4 NN PN PP w1 w2")

G_UCM_block = sp.Matrix([
[AA, 0, 0, AP, AN],
[0, BB, 0, BP, BN],
[0, 0, CC, CP, CN],
[AP, BP, CP, PP, PN],
[AN, BN, CN, PN, NN]
])
Ihis_UCM_block = sp.Matrix([[Ihis_exclude_c0], [Ihis_exclude_c1], [Ihis_exclude_c2], [Ihis_exclude_c3], [Ihis_exclude_c4]])
UCM_block = SubNetwork(name="UCM_block", local_nodes=["P1", "P2", "P3", "P4", "P5"], G_local=G_UCM_block, Ihis_local=Ihis_UCM_block)

G_R2 = sp.Matrix([[gC, -(gC)], [-(gC), gC]])
Ihis_R2 = sp.Matrix([[IC_his0], [-(IC_his0)]])
R2 = SubNetwork(name="R2", local_nodes=["A", "B"], G_local=G_R2, Ihis_local=Ihis_R2)

# Active switch case: Case 1
G_YBox3 = sp.Matrix([[2*(G11 + w1), -G11 - w1, G12, 0, 0, -G11 - w1, 0, 0, -G12, 0], [-G11 - w1, 2*(G11 + w1), -G12, 0, 0, -G11 - w1, G12, 0, 0, 0], [G12, -G12, G22 + G_rc + w2, -G22 - w2, -G_rc, 0, 0, 0, 0, 0], [0, 0, -G22 - w2, 3*(G22 + w2), 0, 0, -G22 - w2, 0, -G22 - w2, 0], [0, 0, -G_rc, 0, G_rc, 0, 0, 0, 0, 0], [-G11 - w1, -G11 - w1, 0, 0, 0, 2*(G11 + w1), -G12, 0, G12, 0], [0, G12, 0, -G22 - w2, 0, -G12, G22 + G_rc + w2, -G_rc, 0, 0], [0, 0, 0, 0, 0, 0, -G_rc, G_rc, 0, 0], [-G12, 0, 0, -G22 - w2, 0, G12, 0, 0, G22 + G_rc + w2, -G_rc], [0, 0, 0, 0, 0, 0, 0, 0, -G_rc, G_rc]])
Ihis_YBox3 = sp.Matrix([[Ihis_A - Ihis_C], [-Ihis_A + Ihis_B], [IAhis_rc + Ihis_a], [-Ihis_a - Ihis_b - Ihis_c], [-IAhis_rc], [-Ihis_B + Ihis_C], [IBhis_rc + Ihis_b], [-IBhis_rc], [IChis_rc + Ihis_c], [-IChis_rc]])
YBox3 = SubNetwork(name="YBox3", local_nodes=["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"], G_local=G_YBox3, Ihis_local=Ihis_YBox3)

asm = NetworkAssembler()
asm.add_subnetwork(UCM_block)
asm.add_subnetwork(R2)
asm.add_subnetwork(YBox3)
asm.connect("UCM_block.P4", "R2.A")
asm.connect("YBox3.P3", "UCM_block.P1")
asm.connect("YBox3.P7", "UCM_block.P2")
asm.connect("YBox3.P9", "UCM_block.P3")
asm.connect("R2.B", "UCM_block.P5")

# Branch current formulas:
# 
# i_R2_P_to_N = (gC) * (V_P - V_N) + IC_his0
# i_YBox3_Tr5_primary_A_to_B = G11*V_A - G11*V_B - G12*V_G + G12*V_x + Ihis_A
i_YBox3_Tr5_secondary_a_to_G = G12*V_A - G12*V_B - G22*V_G + G22*V_x + Ihis_a
i_YBox3_R8_a_to_RC_a = -G_rc*V_RC_a + G_rc*V_x + IAhis_rc
i_YBox3_Tr6_primary_B_to_C = G11*V_B - G11*V_C - G12*V_G + G12*V_y + Ihis_B
i_YBox3_Tr6_secondary_b_to_G = G12*V_B - G12*V_C - G22*V_G + G22*V_y + Ihis_b
i_YBox3_R9_b_to_RC_b = -G_rc*V_RC_b + G_rc*V_y + IBhis_rc
i_YBox3_Tr7_primary_C_to_A = -G11*V_A + G11*V_C - G12*V_G + G12*V_z + Ihis_C
i_YBox3_Tr7_secondary_c_to_G = -G12*V_A + G12*V_C - G22*V_G + G22*V_z + Ihis_c
i_YBox3_R10_c_to_RC_c = -G_rc*V_RC_c + G_rc*V_z + IChis_rc
i_YBox3_R12_A_to_B = V_A*w1 - V_B*w1
i_YBox3_R13_B_to_C = V_B*w1 - V_C*w1
i_YBox3_R14_C_to_A = -V_A*w1 + V_C*w1
i_YBox3_R15_a_to_G = -V_G*w2 + V_x*w2
i_YBox3_R16_b_to_G = -V_G*w2 + V_y*w2
i_YBox3_R17_c_to_G = -V_G*w2 + V_z*w2

# Global system before node reduction
all_nodes = ["A", "B", "C", "G", "RC_a", "RC_b", "RC_c", "x", "y", "z", "P", "N"]
external_nodes = ["A", "B", "C", "G", "RC_a", "RC_b", "RC_c", "P", "N"]
internal_nodes = ["x", "y", "z"]
G_full = sp.Matrix([[2*G11 + 2*w1, -G11 - w1, -G11 - w1, 0, 0, 0, 0, G12, 0, -G12, 0, 0], [-G11 - w1, 2*G11 + 2*w1, -G11 - w1, 0, 0, 0, 0, -G12, G12, 0, 0, 0], [-G11 - w1, -G11 - w1, 2*G11 + 2*w1, 0, 0, 0, 0, 0, -G12, G12, 0, 0], [0, 0, 0, 3*G22 + 3*w2, 0, 0, 0, -G22 - w2, -G22 - w2, -G22 - w2, 0, 0], [0, 0, 0, 0, G_rc, 0, 0, -G_rc, 0, 0, 0, 0], [0, 0, 0, 0, 0, G_rc, 0, 0, -G_rc, 0, 0, 0], [0, 0, 0, 0, 0, 0, G_rc, 0, 0, -G_rc, 0, 0], [G12, -G12, 0, -G22 - w2, -G_rc, 0, 0, AA + G22 + G_rc + w2, 0, 0, AP, AN], [0, G12, -G12, -G22 - w2, 0, -G_rc, 0, 0, BB + G22 + G_rc + w2, 0, BP, BN], [-G12, 0, G12, -G22 - w2, 0, 0, -G_rc, 0, 0, CC + G22 + G_rc + w2, CP, CN], [0, 0, 0, 0, 0, 0, 0, AP, BP, CP, PP + gC, PN - gC], [0, 0, 0, 0, 0, 0, 0, AN, BN, CN, PN - gC, NN + gC]])
Ihis_full = sp.Matrix([[Ihis_A - Ihis_C], [-Ihis_A + Ihis_B], [-Ihis_B + Ihis_C], [-Ihis_a - Ihis_b - Ihis_c], [-IAhis_rc], [-IBhis_rc], [-IChis_rc], [Ihis_exclude_c0 + Ihis_a + IAhis_rc], [Ihis_exclude_c1 + Ihis_b + IBhis_rc], [Ihis_exclude_c2 + Ihis_c + IChis_rc], [Ihis_exclude_c3 + IC_his0], [Ihis_exclude_c4 - IC_his0]])

print("Before reduction: G_full")
print(G_full)
print("Before reduction: Ihis_full")
print(Ihis_full)

# Global system after eliminating internal nodes
reduced_external_nodes = ["A", "B", "C", "G", "RC_a", "RC_b", "RC_c", "P", "N"]
eliminated_internal_nodes = ["x", "y", "z"]
G_red = sp.Matrix([[2*G11 - G12**2/(CC + G22 + G_rc + w2) - G12**2/(AA + G22 + G_rc + w2) + 2*w1, -G11 + G12**2/(AA + G22 + G_rc + w2) - w1, -G11 + G12**2/(CC + G22 + G_rc + w2) - w1, G12*(-G22 - w2)/(CC + G22 + G_rc + w2) - G12*(-G22 - w2)/(AA + G22 + G_rc + w2), G12*G_rc/(AA + G22 + G_rc + w2), 0, -G12*G_rc/(CC + G22 + G_rc + w2), -AP*G12/(AA + G22 + G_rc + w2) + CP*G12/(CC + G22 + G_rc + w2), -AN*G12/(AA + G22 + G_rc + w2) + CN*G12/(CC + G22 + G_rc + w2)], [-G11 + G12**2/(AA + G22 + G_rc + w2) - w1, 2*G11 - G12**2/(BB + G22 + G_rc + w2) - G12**2/(AA + G22 + G_rc + w2) + 2*w1, -G11 + G12**2/(BB + G22 + G_rc + w2) - w1, -G12*(-G22 - w2)/(BB + G22 + G_rc + w2) + G12*(-G22 - w2)/(AA + G22 + G_rc + w2), -G12*G_rc/(AA + G22 + G_rc + w2), G12*G_rc/(BB + G22 + G_rc + w2), 0, AP*G12/(AA + G22 + G_rc + w2) - BP*G12/(BB + G22 + G_rc + w2), AN*G12/(AA + G22 + G_rc + w2) - BN*G12/(BB + G22 + G_rc + w2)], [-G11 + G12**2/(CC + G22 + G_rc + w2) - w1, -G11 + G12**2/(BB + G22 + G_rc + w2) - w1, 2*G11 - G12**2/(CC + G22 + G_rc + w2) - G12**2/(BB + G22 + G_rc + w2) + 2*w1, -G12*(-G22 - w2)/(CC + G22 + G_rc + w2) + G12*(-G22 - w2)/(BB + G22 + G_rc + w2), 0, -G12*G_rc/(BB + G22 + G_rc + w2), G12*G_rc/(CC + G22 + G_rc + w2), BP*G12/(BB + G22 + G_rc + w2) - CP*G12/(CC + G22 + G_rc + w2), BN*G12/(BB + G22 + G_rc + w2) - CN*G12/(CC + G22 + G_rc + w2)], [G12*(-G22 - w2)/(CC + G22 + G_rc + w2) - G12*(-G22 - w2)/(AA + G22 + G_rc + w2), -G12*(-G22 - w2)/(BB + G22 + G_rc + w2) + G12*(-G22 - w2)/(AA + G22 + G_rc + w2), -G12*(-G22 - w2)/(CC + G22 + G_rc + w2) + G12*(-G22 - w2)/(BB + G22 + G_rc + w2), 3*G22 + 3*w2 - (-G22 - w2)**2/(CC + G22 + G_rc + w2) - (-G22 - w2)**2/(BB + G22 + G_rc + w2) - (-G22 - w2)**2/(AA + G22 + G_rc + w2), G_rc*(-G22 - w2)/(AA + G22 + G_rc + w2), G_rc*(-G22 - w2)/(BB + G22 + G_rc + w2), G_rc*(-G22 - w2)/(CC + G22 + G_rc + w2), -AP*(-G22 - w2)/(AA + G22 + G_rc + w2) - BP*(-G22 - w2)/(BB + G22 + G_rc + w2) - CP*(-G22 - w2)/(CC + G22 + G_rc + w2), -AN*(-G22 - w2)/(AA + G22 + G_rc + w2) - BN*(-G22 - w2)/(BB + G22 + G_rc + w2) - CN*(-G22 - w2)/(CC + G22 + G_rc + w2)], [G12*G_rc/(AA + G22 + G_rc + w2), -G12*G_rc/(AA + G22 + G_rc + w2), 0, G_rc*(-G22 - w2)/(AA + G22 + G_rc + w2), -G_rc**2/(AA + G22 + G_rc + w2) + G_rc, 0, 0, AP*G_rc/(AA + G22 + G_rc + w2), AN*G_rc/(AA + G22 + G_rc + w2)], [0, G12*G_rc/(BB + G22 + G_rc + w2), -G12*G_rc/(BB + G22 + G_rc + w2), G_rc*(-G22 - w2)/(BB + G22 + G_rc + w2), 0, -G_rc**2/(BB + G22 + G_rc + w2) + G_rc, 0, BP*G_rc/(BB + G22 + G_rc + w2), BN*G_rc/(BB + G22 + G_rc + w2)], [-G12*G_rc/(CC + G22 + G_rc + w2), 0, G12*G_rc/(CC + G22 + G_rc + w2), G_rc*(-G22 - w2)/(CC + G22 + G_rc + w2), 0, 0, -G_rc**2/(CC + G22 + G_rc + w2) + G_rc, CP*G_rc/(CC + G22 + G_rc + w2), CN*G_rc/(CC + G22 + G_rc + w2)], [-AP*G12/(AA + G22 + G_rc + w2) + CP*G12/(CC + G22 + G_rc + w2), AP*G12/(AA + G22 + G_rc + w2) - BP*G12/(BB + G22 + G_rc + w2), BP*G12/(BB + G22 + G_rc + w2) - CP*G12/(CC + G22 + G_rc + w2), -AP*(-G22 - w2)/(AA + G22 + G_rc + w2) - BP*(-G22 - w2)/(BB + G22 + G_rc + w2) - CP*(-G22 - w2)/(CC + G22 + G_rc + w2), AP*G_rc/(AA + G22 + G_rc + w2), BP*G_rc/(BB + G22 + G_rc + w2), CP*G_rc/(CC + G22 + G_rc + w2), -AP**2/(AA + G22 + G_rc + w2) - BP**2/(BB + G22 + G_rc + w2) - CP**2/(CC + G22 + G_rc + w2) + PP + gC, -AN*AP/(AA + G22 + G_rc + w2) - BN*BP/(BB + G22 + G_rc + w2) - CN*CP/(CC + G22 + G_rc + w2) + PN - gC], [-AN*G12/(AA + G22 + G_rc + w2) + CN*G12/(CC + G22 + G_rc + w2), AN*G12/(AA + G22 + G_rc + w2) - BN*G12/(BB + G22 + G_rc + w2), BN*G12/(BB + G22 + G_rc + w2) - CN*G12/(CC + G22 + G_rc + w2), -AN*(-G22 - w2)/(AA + G22 + G_rc + w2) - BN*(-G22 - w2)/(BB + G22 + G_rc + w2) - CN*(-G22 - w2)/(CC + G22 + G_rc + w2), AN*G_rc/(AA + G22 + G_rc + w2), BN*G_rc/(BB + G22 + G_rc + w2), CN*G_rc/(CC + G22 + G_rc + w2), -AN*AP/(AA + G22 + G_rc + w2) - BN*BP/(BB + G22 + G_rc + w2) - CN*CP/(CC + G22 + G_rc + w2) + PN - gC, -AN**2/(AA + G22 + G_rc + w2) - BN**2/(BB + G22 + G_rc + w2) - CN**2/(CC + G22 + G_rc + w2) + NN + gC]])
Ihis_red = sp.Matrix([[-G12*(IAhis_rc + Ihis_a + Ihis_exclude_c0)/(AA + G22 + G_rc + w2) + G12*(IChis_rc + Ihis_c + Ihis_exclude_c2)/(CC + G22 + G_rc + w2) + Ihis_A - Ihis_C], [G12*(IAhis_rc + Ihis_a + Ihis_exclude_c0)/(AA + G22 + G_rc + w2) - G12*(IBhis_rc + Ihis_b + Ihis_exclude_c1)/(BB + G22 + G_rc + w2) - Ihis_A + Ihis_B], [G12*(IBhis_rc + Ihis_b + Ihis_exclude_c1)/(BB + G22 + G_rc + w2) - G12*(IChis_rc + Ihis_c + Ihis_exclude_c2)/(CC + G22 + G_rc + w2) - Ihis_B + Ihis_C], [-Ihis_a - Ihis_b - Ihis_c - (-G22 - w2)*(IAhis_rc + Ihis_a + Ihis_exclude_c0)/(AA + G22 + G_rc + w2) - (-G22 - w2)*(IBhis_rc + Ihis_b + Ihis_exclude_c1)/(BB + G22 + G_rc + w2) - (-G22 - w2)*(IChis_rc + Ihis_c + Ihis_exclude_c2)/(CC + G22 + G_rc + w2)], [G_rc*(IAhis_rc + Ihis_a + Ihis_exclude_c0)/(AA + G22 + G_rc + w2) - IAhis_rc], [G_rc*(IBhis_rc + Ihis_b + Ihis_exclude_c1)/(BB + G22 + G_rc + w2) - IBhis_rc], [G_rc*(IChis_rc + Ihis_c + Ihis_exclude_c2)/(CC + G22 + G_rc + w2) - IChis_rc], [-AP*(IAhis_rc + Ihis_a + Ihis_exclude_c0)/(AA + G22 + G_rc + w2) - BP*(IBhis_rc + Ihis_b + Ihis_exclude_c1)/(BB + G22 + G_rc + w2) - CP*(IChis_rc + Ihis_c + Ihis_exclude_c2)/(CC + G22 + G_rc + w2) + IC_his0 + Ihis_exclude_c3], [-AN*(IAhis_rc + Ihis_a + Ihis_exclude_c0)/(AA + G22 + G_rc + w2) - BN*(IBhis_rc + Ihis_b + Ihis_exclude_c1)/(BB + G22 + G_rc + w2) - CN*(IChis_rc + Ihis_c + Ihis_exclude_c2)/(CC + G22 + G_rc + w2) - IC_his0 + Ihis_exclude_c4]])

print("After reduction: G_red")
print(G_red)
print("After reduction: Ihis_red")
print(Ihis_red)

# Eliminated internal-node voltage recovery
# V_internal = K_v * V_external + K_h
K_v = sp.Matrix([[-G12/(AA + G22 + G_rc + w2), G12/(AA + G22 + G_rc + w2), 0, (G22 + w2)/(AA + G22 + G_rc + w2), G_rc/(AA + G22 + G_rc + w2), 0, 0, -AP/(AA + G22 + G_rc + w2), -AN/(AA + G22 + G_rc + w2)], [0, -G12/(BB + G22 + G_rc + w2), G12/(BB + G22 + G_rc + w2), (G22 + w2)/(BB + G22 + G_rc + w2), 0, G_rc/(BB + G22 + G_rc + w2), 0, -BP/(BB + G22 + G_rc + w2), -BN/(BB + G22 + G_rc + w2)], [G12/(CC + G22 + G_rc + w2), 0, -G12/(CC + G22 + G_rc + w2), (G22 + w2)/(CC + G22 + G_rc + w2), 0, 0, G_rc/(CC + G22 + G_rc + w2), -CP/(CC + G22 + G_rc + w2), -CN/(CC + G22 + G_rc + w2)]])
K_h = sp.Matrix([[(-IAhis_rc - Ihis_a - Ihis_exclude_c0)/(AA + G22 + G_rc + w2)], [(-IBhis_rc - Ihis_b - Ihis_exclude_c1)/(BB + G22 + G_rc + w2)], [(-IChis_rc - Ihis_c - Ihis_exclude_c2)/(CC + G22 + G_rc + w2)]])
internal_voltage_recovery = {
    "V_x": "(-G12/(AA + G22 + G_rc + w2))*V_A + (G12/(AA + G22 + G_rc + w2))*V_B + ((G22 + w2)/(AA + G22 + G_rc + w2))*V_G + (G_rc/(AA + G22 + G_rc + w2))*V_RC_a + (-AP/(AA + G22 + G_rc + w2))*V_P + (-AN/(AA + G22 + G_rc + w2))*V_N + ((-IAhis_rc - Ihis_a - Ihis_exclude_c0)/(AA + G22 + G_rc + w2))",
    "V_y": "(-G12/(BB + G22 + G_rc + w2))*V_B + (G12/(BB + G22 + G_rc + w2))*V_C + ((G22 + w2)/(BB + G22 + G_rc + w2))*V_G + (G_rc/(BB + G22 + G_rc + w2))*V_RC_b + (-BP/(BB + G22 + G_rc + w2))*V_P + (-BN/(BB + G22 + G_rc + w2))*V_N + ((-IBhis_rc - Ihis_b - Ihis_exclude_c1)/(BB + G22 + G_rc + w2))",
    "V_z": "(G12/(CC + G22 + G_rc + w2))*V_A + (-G12/(CC + G22 + G_rc + w2))*V_C + ((G22 + w2)/(CC + G22 + G_rc + w2))*V_G + (G_rc/(CC + G22 + G_rc + w2))*V_RC_c + (-CP/(CC + G22 + G_rc + w2))*V_P + (-CN/(CC + G22 + G_rc + w2))*V_N + ((-IChis_rc - Ihis_c - Ihis_exclude_c2)/(CC + G22 + G_rc + w2))",
}
print("Eliminated internal-node voltage recovery")
for node, expression in internal_voltage_recovery.items():
    print(f"{node} = {expression}")