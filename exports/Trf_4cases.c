#include <matrixLIB.h>
#include <builtin_MATH.h>
/* Multi-case alias-template C draft.
   Each case-resolved alias is assigned a full source value first;
   the normal structured matrix DAG is generated exactly once. */
/* RTDS-style C draft for structured node elimination.
   RAM math reference may use matrix_Add/Sub/Mul on raw arrays.
   Runtime sections use MATRIX_ matrixDim/register/condition plus set_CODE,
   matrix_mult_CODE, matrix_matXvec_CODE, matrix_add_CODE/subtract_CODE,
   matrix_scalarMult_CODE, MATH_matx_invert,
   mat_2x2_sym_inv_code, mat_3x3_sym_inv_code. */


/* RTDS lifecycle placement for a network with no eliminated internal nodes.
   No Schur complement is required: use the original G matrix and Ihis vector directly. */
STATIC:

    int C1_case_id = 0;
    double sourceGI_C1_case0_tmp0 = 0.0;
    double sourceGI_C1_case0_tmp1 = 0.0;
    double sourceGI_C1_case0_tmp2 = 0.0;
    double sourceGI_C1_case0_tmp3 = 0.0;
    double sourceGI_C1_case0_tmp4 = 0.0;
    double sourceGI_C1_case0_tmp5 = 0.0;
    double sourceGI_C1_case0_tmp6 = 0.0;
    double sourceGI_C1_case0_tmp7 = 0.0;
    double sourceGI_C1_case0_tmp8 = 0.0;
    double sourceGI_C1_case0_tmp9 = 0.0;
    double sourceGI_C1_case0_tmp10 = 0.0;
    double sourceGI_C1_case0_tmp11 = 0.0;
    double sourceGI_C1_case0_tmp12 = 0.0;
    double sourceGI_C1_case0_tmp13 = 0.0;
    double sourceGI_C1_case1_tmp0 = 0.0;
    double sourceGI_C1_case1_tmp1 = 0.0;
    double sourceGI_C1_case2_tmp0 = 0.0;
    double sourceGI_C1_case2_tmp1 = 0.0;
    double G11 = 0.0;
    double G12 = 0.0;
    double G22 = 0.0;
    double Gc = 0.0;
    double IhisC1 = 0.0;
    double IhisC2 = 0.0;
    double Ihis_p = 0.0;
    double Ihis_s = 0.0;

    /* Runtime matrix objects */
    /* No runtime MATRIX_ objects are required on this no-elimination path. */
    /* User Ihis/history symbols */
    double multcase_Ihis_C1_N1 = 0.0;
    double multcase_Ihis_C1_N2 = 0.0;
    double multcase_Ihis_C1_N3 = 0.0;
    double multcase_Ihis_C1_N4 = 0.0;

LOCAL_STATIC:
    /* User RAM-only G symbols */
    double multcase_G_C1_N1_N1 = 0.0;
    double multcase_G_C1_N1_N2 = 0.0;
    double multcase_G_C1_N1_N3 = 0.0;
    double multcase_G_C1_N1_N4 = 0.0;
    double multcase_G_C1_N2_N2 = 0.0;
    double multcase_G_C1_N2_N3 = 0.0;
    double multcase_G_C1_N2_N4 = 0.0;
    double multcase_G_C1_N3_N3 = 0.0;
    double multcase_G_C1_N3_N4 = 0.0;
    double multcase_G_C1_N4_N4 = 0.0;

RAM_PASS1:
    /* Decode the optional global case selector into per-element local cases. */
    switch (case_id) {
    case 0: /* case 0: Custom1 = BothC; C1=case0 */
        C1_case_id = 0;
        break;
    case 1: /* case 1: Custom1 = rightC; C1=case1 */
        C1_case_id = 1;
        break;
    case 2: /* case 2: Custom1 = leftC; C1=case2 */
        C1_case_id = 2;
        break;
    case 3: /* case 3: Custom1 = NoC; C1=case3 */
        C1_case_id = 3;
        break;
    default:
        C1_case_id = 0;
        break;
    }
    /* Resolve RAM-safe source temporaries shared by G and Ihis aliases. */
    switch (C1_case_id) {
    case 0:
        sourceGI_C1_case0_tmp0 = 1.0/(G11 + Gc);
        sourceGI_C1_case0_tmp1 = pow(Gc, 2.0);
        sourceGI_C1_case0_tmp2 = G11*G22;
        sourceGI_C1_case0_tmp3 = G11*Gc;
        sourceGI_C1_case0_tmp4 = pow(G12, 2.0);
        sourceGI_C1_case0_tmp5 = sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4;
        sourceGI_C1_case0_tmp6 = 1.0/(-G11*sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4 + G22*Gc - Gc*sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4 + sourceGI_C1_case0_tmp1 + sourceGI_C1_case0_tmp2 + sourceGI_C1_case0_tmp3);
        sourceGI_C1_case0_tmp7 = G12*sourceGI_C1_case0_tmp6;
        sourceGI_C1_case0_tmp8 = pow(G11, 2.0);
        sourceGI_C1_case0_tmp9 = 1.0/(2.0*G11*G22*Gc - 2.0*G11*Gc*sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4 + 2.0*G11*sourceGI_C1_case0_tmp1 + G22*sourceGI_C1_case0_tmp1 + G22*sourceGI_C1_case0_tmp8 + pow(Gc, 3.0) + Gc*sourceGI_C1_case0_tmp8 - sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp1*sourceGI_C1_case0_tmp4 - sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp8);
        sourceGI_C1_case0_tmp10 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;
        sourceGI_C1_case0_tmp11 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp6;
        sourceGI_C1_case0_tmp12 = 1.0/(G22 + Gc - sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4);
        sourceGI_C1_case0_tmp13 = pow(G12, 3.0)*sourceGI_C1_case0_tmp9;
        break;
    case 1:
        sourceGI_C1_case1_tmp0 = 1.0/(G22 + Gc);
        sourceGI_C1_case1_tmp1 = G12*sourceGI_C1_case1_tmp0;
        break;
    case 2:
        sourceGI_C1_case2_tmp0 = 1.0/(G11 + Gc);
        sourceGI_C1_case2_tmp1 = Gc*sourceGI_C1_case2_tmp0;
        break;
    default:
        sourceGI_C1_case0_tmp0 = 1.0/(G11 + Gc);
        sourceGI_C1_case0_tmp1 = pow(Gc, 2.0);
        sourceGI_C1_case0_tmp2 = G11*G22;
        sourceGI_C1_case0_tmp3 = G11*Gc;
        sourceGI_C1_case0_tmp4 = pow(G12, 2.0);
        sourceGI_C1_case0_tmp5 = sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4;
        sourceGI_C1_case0_tmp6 = 1.0/(-G11*sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4 + G22*Gc - Gc*sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4 + sourceGI_C1_case0_tmp1 + sourceGI_C1_case0_tmp2 + sourceGI_C1_case0_tmp3);
        sourceGI_C1_case0_tmp7 = G12*sourceGI_C1_case0_tmp6;
        sourceGI_C1_case0_tmp8 = pow(G11, 2.0);
        sourceGI_C1_case0_tmp9 = 1.0/(2.0*G11*G22*Gc - 2.0*G11*Gc*sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4 + 2.0*G11*sourceGI_C1_case0_tmp1 + G22*sourceGI_C1_case0_tmp1 + G22*sourceGI_C1_case0_tmp8 + pow(Gc, 3.0) + Gc*sourceGI_C1_case0_tmp8 - sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp1*sourceGI_C1_case0_tmp4 - sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp8);
        sourceGI_C1_case0_tmp10 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;
        sourceGI_C1_case0_tmp11 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp6;
        sourceGI_C1_case0_tmp12 = 1.0/(G22 + Gc - sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp4);
        sourceGI_C1_case0_tmp13 = pow(G12, 3.0)*sourceGI_C1_case0_tmp9;
        break;
    }
    /* Resolve RAM multi-case effective aliases as full values, never deltas. */
    switch (C1_case_id) {
    case 0:
    {
        double sourceG_C1_case0_tmp0 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;
        double sourceG_C1_case0_tmp1 = Gc*sourceGI_C1_case0_tmp0;
        double sourceG_C1_case0_tmp2 = Gc*sourceGI_C1_case0_tmp6;
        double sourceG_C1_case0_tmp3 = -sourceGI_C1_case0_tmp4*sourceG_C1_case0_tmp2;
        double sourceG_C1_case0_tmp4 = G12*sourceGI_C1_case0_tmp6;
        double sourceG_C1_case0_tmp5 = pow(G12, 3.0);
        double sourceG_C1_case0_tmp6 = sourceGI_C1_case0_tmp9*sourceG_C1_case0_tmp5;
        double sourceG_C1_case0_tmp7 = 2.0*sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp6;
        double sourceG_C1_case0_tmp8 = G12*sourceGI_C1_case0_tmp12;
        double sourceG_C1_case0_tmp9 = G11*G12;
        multcase_G_C1_N1_N1 = -Gc + sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp1 + sourceGI_C1_case0_tmp1*sourceG_C1_case0_tmp0;
        multcase_G_C1_N1_N2 = G11*Gc*sourceG_C1_case0_tmp0 + G11*sourceG_C1_case0_tmp1 + sourceG_C1_case0_tmp3;
        multcase_G_C1_N1_N3 = sourceGI_C1_case0_tmp1*sourceG_C1_case0_tmp4;
        multcase_G_C1_N1_N4 = -G12*G22*sourceG_C1_case0_tmp2 + G12*sourceG_C1_case0_tmp1 + Gc*sourceG_C1_case0_tmp6;
        multcase_G_C1_N2_N2 = -G11*sourceG_C1_case0_tmp7 - G11 + sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp8 + sourceGI_C1_case0_tmp12*sourceGI_C1_case0_tmp4 + sourceGI_C1_case0_tmp8*sourceG_C1_case0_tmp0;
        multcase_G_C1_N2_N3 = -Gc*sourceG_C1_case0_tmp8 + sourceG_C1_case0_tmp2*sourceG_C1_case0_tmp9;
        multcase_G_C1_N2_N4 = -G11*G22*sourceG_C1_case0_tmp4 + G11*sourceG_C1_case0_tmp6 - G12 + G22*sourceG_C1_case0_tmp8 + sourceGI_C1_case0_tmp0*sourceG_C1_case0_tmp9 - sourceGI_C1_case0_tmp6*sourceG_C1_case0_tmp5;
        multcase_G_C1_N3_N3 = -Gc + sourceGI_C1_case0_tmp1*sourceGI_C1_case0_tmp12;
        multcase_G_C1_N3_N4 = -G22*Gc*sourceGI_C1_case0_tmp12 - sourceG_C1_case0_tmp3;
        multcase_G_C1_N4_N4 = pow(G12, 4.0)*sourceGI_C1_case0_tmp9 + pow(G22, 2.0)*sourceGI_C1_case0_tmp12 - G22*sourceG_C1_case0_tmp7 - G22 + sourceGI_C1_case0_tmp5;
        break;
    }
    case 1:
    {
        double sourceG_C1_case1_tmp0 = G11 - pow(G12, 2.0)*sourceGI_C1_case1_tmp0;
        double sourceG_C1_case1_tmp1 = -sourceG_C1_case1_tmp0;
        double sourceG_C1_case1_tmp2 = G12*sourceGI_C1_case1_tmp0;
        double sourceG_C1_case1_tmp3 = Gc*sourceG_C1_case1_tmp2;
        double sourceG_C1_case1_tmp4 = -G12 + G22*sourceG_C1_case1_tmp2;
        multcase_G_C1_N1_N1 = sourceG_C1_case1_tmp1;
        multcase_G_C1_N1_N2 = sourceG_C1_case1_tmp0;
        multcase_G_C1_N1_N3 = sourceG_C1_case1_tmp3;
        multcase_G_C1_N1_N4 = -sourceG_C1_case1_tmp4;
        multcase_G_C1_N2_N2 = sourceG_C1_case1_tmp1;
        multcase_G_C1_N2_N3 = -sourceG_C1_case1_tmp3;
        multcase_G_C1_N2_N4 = sourceG_C1_case1_tmp4;
        multcase_G_C1_N3_N3 = pow(Gc, 2.0)*sourceGI_C1_case1_tmp0 - Gc;
        multcase_G_C1_N3_N4 = -G22*Gc*sourceGI_C1_case1_tmp0;
        multcase_G_C1_N4_N4 = pow(G22, 2.0)*sourceGI_C1_case1_tmp0 - G22;
        break;
    }
    case 2:
    {
        double sourceG_C1_case2_tmp0 = Gc*sourceGI_C1_case2_tmp0;
        double sourceG_C1_case2_tmp1 = G12*sourceG_C1_case2_tmp0;
        double sourceG_C1_case2_tmp2 = G11*G12*sourceGI_C1_case2_tmp0 - G12;
        double sourceG_C1_case2_tmp3 = pow(G12, 2.0)*sourceGI_C1_case2_tmp0 - G22;
        multcase_G_C1_N1_N1 = pow(Gc, 2.0)*sourceGI_C1_case2_tmp0 - Gc;
        multcase_G_C1_N1_N2 = G11*sourceG_C1_case2_tmp0;
        multcase_G_C1_N1_N3 = sourceG_C1_case2_tmp1;
        multcase_G_C1_N1_N4 = sourceG_C1_case2_tmp1;
        multcase_G_C1_N2_N2 = pow(G11, 2.0)*sourceGI_C1_case2_tmp0 - G11;
        multcase_G_C1_N2_N3 = sourceG_C1_case2_tmp2;
        multcase_G_C1_N2_N4 = sourceG_C1_case2_tmp2;
        multcase_G_C1_N3_N3 = sourceG_C1_case2_tmp3;
        multcase_G_C1_N3_N4 = sourceG_C1_case2_tmp3;
        multcase_G_C1_N4_N4 = sourceG_C1_case2_tmp3;
        break;
    }
    case 3:
        multcase_G_C1_N1_N1 = -G11;
        multcase_G_C1_N1_N2 = G11;
        multcase_G_C1_N1_N3 = G12;
        multcase_G_C1_N1_N4 = G12;
        multcase_G_C1_N2_N2 = -G11;
        multcase_G_C1_N2_N3 = -G12;
        multcase_G_C1_N2_N4 = -G12;
        multcase_G_C1_N3_N3 = -G22;
        multcase_G_C1_N3_N4 = -G22;
        multcase_G_C1_N4_N4 = -G22;
        break;
    default:
    {
        double sourceG_C1_default_tmp0 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;
        double sourceG_C1_default_tmp1 = Gc*sourceGI_C1_case0_tmp0;
        double sourceG_C1_default_tmp2 = Gc*sourceGI_C1_case0_tmp6;
        double sourceG_C1_default_tmp3 = -sourceGI_C1_case0_tmp4*sourceG_C1_default_tmp2;
        double sourceG_C1_default_tmp4 = G12*sourceGI_C1_case0_tmp6;
        double sourceG_C1_default_tmp5 = pow(G12, 3.0);
        double sourceG_C1_default_tmp6 = sourceGI_C1_case0_tmp9*sourceG_C1_default_tmp5;
        double sourceG_C1_default_tmp7 = 2.0*sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp6;
        double sourceG_C1_default_tmp8 = G12*sourceGI_C1_case0_tmp12;
        double sourceG_C1_default_tmp9 = G11*G12;
        multcase_G_C1_N1_N1 = -Gc + sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp1 + sourceGI_C1_case0_tmp1*sourceG_C1_default_tmp0;
        multcase_G_C1_N1_N2 = G11*Gc*sourceG_C1_default_tmp0 + G11*sourceG_C1_default_tmp1 + sourceG_C1_default_tmp3;
        multcase_G_C1_N1_N3 = sourceGI_C1_case0_tmp1*sourceG_C1_default_tmp4;
        multcase_G_C1_N1_N4 = -G12*G22*sourceG_C1_default_tmp2 + G12*sourceG_C1_default_tmp1 + Gc*sourceG_C1_default_tmp6;
        multcase_G_C1_N2_N2 = -G11*sourceG_C1_default_tmp7 - G11 + sourceGI_C1_case0_tmp0*sourceGI_C1_case0_tmp8 + sourceGI_C1_case0_tmp12*sourceGI_C1_case0_tmp4 + sourceGI_C1_case0_tmp8*sourceG_C1_default_tmp0;
        multcase_G_C1_N2_N3 = -Gc*sourceG_C1_default_tmp8 + sourceG_C1_default_tmp2*sourceG_C1_default_tmp9;
        multcase_G_C1_N2_N4 = -G11*G22*sourceG_C1_default_tmp4 + G11*sourceG_C1_default_tmp6 - G12 + G22*sourceG_C1_default_tmp8 + sourceGI_C1_case0_tmp0*sourceG_C1_default_tmp9 - sourceGI_C1_case0_tmp6*sourceG_C1_default_tmp5;
        multcase_G_C1_N3_N3 = -Gc + sourceGI_C1_case0_tmp1*sourceGI_C1_case0_tmp12;
        multcase_G_C1_N3_N4 = -G22*Gc*sourceGI_C1_case0_tmp12 - sourceG_C1_default_tmp3;
        multcase_G_C1_N4_N4 = pow(G12, 4.0)*sourceGI_C1_case0_tmp9 + pow(G22, 2.0)*sourceGI_C1_case0_tmp12 - G22*sourceG_C1_default_tmp7 - G22 + sourceGI_C1_case0_tmp5;
        break;
    }
    }
    /* ************************************************************************
     * RAM-SIDE G MATRIX VALUE SETUP
     * No internal nodes are selected, so fixed RAM G stamping uses the original G matrix.
     * Assign or compute every G-related value before writing g_mat_over.
     * Dynamic entries are registered in GVALUES and refreshed in CODE.
     * ************************************************************************ */



    g_mat_nods[0] = getNodeNum(comp, "N1");
    g_mat_nods[1] = getNodeNum(comp, "N2");
    g_mat_nods[2] = getNodeNum(comp, "N3");
    g_mat_nods[3] = getNodeNum(comp, "N4");
    /* g_mat_over is provided by the PSYS/CBuilder runtime; initialize, do not define it here. */
    for (int row = 0; row < 4; row++) {
        for (int col = 0; col < 4; col++) {
            g_mat_over[row][col] = 0.0;
        }
    }
    g_mat_over[0][0] = -multcase_G_C1_N1_N1;
    g_mat_over[0][1] = -multcase_G_C1_N1_N2;
    g_mat_over[0][2] = multcase_G_C1_N1_N3;
    g_mat_over[0][3] = -multcase_G_C1_N1_N4;
    g_mat_over[1][0] = -multcase_G_C1_N1_N2;
    g_mat_over[1][1] = -multcase_G_C1_N2_N2;
    g_mat_over[1][2] = multcase_G_C1_N2_N3;
    g_mat_over[1][3] = -multcase_G_C1_N2_N4;
    g_mat_over[2][0] = multcase_G_C1_N1_N3;
    g_mat_over[2][1] = multcase_G_C1_N2_N3;
    g_mat_over[2][2] = -multcase_G_C1_N3_N3;
    g_mat_over[2][3] = multcase_G_C1_N3_N4;
    g_mat_over[3][0] = -multcase_G_C1_N1_N4;
    g_mat_over[3][1] = -multcase_G_C1_N2_N4;
    g_mat_over[3][2] = multcase_G_C1_N3_N4;
    g_mat_over[3][3] = -multcase_G_C1_N4_N4;
    setupGMatrix(4);


CODE:
BEGIN_T0:
    /* Resolve CODE_PER_STEP multi-case effective aliases as full values, never deltas. */
    switch (C1_case_id) {
    case 0:
    {
        double sourceG_C1_case0_tmp0 = G12*sourceGI_C1_case0_tmp6;
        double sourceG_C1_case0_tmp1 = Gc*sourceG_C1_case0_tmp0;
        double sourceG_C1_case0_tmp2 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;
        double sourceG_C1_case0_tmp3 = IhisC1*sourceG_C1_case0_tmp2;
        double sourceG_C1_case0_tmp4 = G11*sourceGI_C1_case0_tmp0;
        double sourceG_C1_case0_tmp5 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp6;
        double sourceG_C1_case0_tmp6 = G12*sourceGI_C1_case0_tmp12;
        double sourceG_C1_case0_tmp7 = G11*sourceG_C1_case0_tmp0;
        double sourceG_C1_case0_tmp8 = Gc*sourceGI_C1_case0_tmp12;
        double sourceG_C1_case0_tmp9 = G12*sourceGI_C1_case0_tmp0;
        double sourceG_C1_case0_tmp10 = pow(G12, 3.0)*sourceGI_C1_case0_tmp9;
        double sourceG_C1_case0_tmp11 = G22*sourceGI_C1_case0_tmp12;
        double sourceG_C1_case0_tmp12 = G22*sourceG_C1_case0_tmp0;
        multcase_Ihis_C1_N1 = -Gc*IhisC1*sourceGI_C1_case0_tmp0 + Gc*Ihis_p*sourceGI_C1_case0_tmp0 + Gc*Ihis_p*sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9 - Gc*sourceG_C1_case0_tmp3 + IhisC1 - IhisC2*sourceG_C1_case0_tmp1 - Ihis_s*sourceG_C1_case0_tmp1;
        multcase_Ihis_C1_N2 = -G11*Ihis_p*sourceG_C1_case0_tmp2 + G11*sourceG_C1_case0_tmp3 + IhisC1*sourceG_C1_case0_tmp4 - IhisC1*sourceG_C1_case0_tmp5 - IhisC2*sourceG_C1_case0_tmp6 + IhisC2*sourceG_C1_case0_tmp7 - Ihis_p*sourceG_C1_case0_tmp4 + Ihis_p*sourceG_C1_case0_tmp5 + Ihis_p - Ihis_s*sourceG_C1_case0_tmp6 + Ihis_s*sourceG_C1_case0_tmp7;
        multcase_Ihis_C1_N3 = -Gc*Ihis_p*sourceG_C1_case0_tmp0 + IhisC1*sourceG_C1_case0_tmp1 + IhisC2*sourceG_C1_case0_tmp8 - IhisC2 + Ihis_s*sourceG_C1_case0_tmp8;
        multcase_Ihis_C1_N4 = IhisC1*sourceG_C1_case0_tmp10 - IhisC1*sourceG_C1_case0_tmp12 + IhisC1*sourceG_C1_case0_tmp9 - IhisC2*sourceG_C1_case0_tmp11 + IhisC2*sourceG_C1_case0_tmp5 - Ihis_p*sourceG_C1_case0_tmp10 + Ihis_p*sourceG_C1_case0_tmp12 - Ihis_p*sourceG_C1_case0_tmp9 - Ihis_s*sourceG_C1_case0_tmp11 + Ihis_s*sourceG_C1_case0_tmp5 + Ihis_s;
        break;
    }
    case 1:
    {
        double sourceG_C1_case1_tmp0 = G12*sourceGI_C1_case1_tmp0;
        double sourceG_C1_case1_tmp1 = -IhisC2*sourceG_C1_case1_tmp0 + Ihis_p - Ihis_s*sourceG_C1_case1_tmp0;
        double sourceG_C1_case1_tmp2 = Gc*sourceGI_C1_case1_tmp0;
        double sourceG_C1_case1_tmp3 = G22*sourceGI_C1_case1_tmp0;
        multcase_Ihis_C1_N1 = sourceG_C1_case1_tmp1;
        multcase_Ihis_C1_N2 = sourceG_C1_case1_tmp1;
        multcase_Ihis_C1_N3 = IhisC2*sourceG_C1_case1_tmp2 - IhisC2 + Ihis_s*sourceG_C1_case1_tmp2;
        multcase_Ihis_C1_N4 = -IhisC2*sourceG_C1_case1_tmp3 - Ihis_s*sourceG_C1_case1_tmp3 + Ihis_s;
        break;
    }
    case 2:
    {
        double sourceG_C1_case2_tmp0 = Gc*sourceGI_C1_case2_tmp0;
        double sourceG_C1_case2_tmp1 = G11*sourceGI_C1_case2_tmp0;
        double sourceG_C1_case2_tmp2 = G12*sourceGI_C1_case2_tmp0;
        double sourceG_C1_case2_tmp3 = IhisC1*sourceG_C1_case2_tmp2 - Ihis_p*sourceG_C1_case2_tmp2 + Ihis_s;
        multcase_Ihis_C1_N1 = -IhisC1*sourceG_C1_case2_tmp0 + IhisC1 + Ihis_p*sourceG_C1_case2_tmp0;
        multcase_Ihis_C1_N2 = IhisC1*sourceG_C1_case2_tmp1 - Ihis_p*sourceG_C1_case2_tmp1 + Ihis_p;
        multcase_Ihis_C1_N3 = sourceG_C1_case2_tmp3;
        multcase_Ihis_C1_N4 = sourceG_C1_case2_tmp3;
        break;
    }
    case 3:
        multcase_Ihis_C1_N1 = Ihis_p;
        multcase_Ihis_C1_N2 = Ihis_p;
        multcase_Ihis_C1_N3 = Ihis_s;
        multcase_Ihis_C1_N4 = Ihis_s;
        break;
    default:
    {
        double sourceG_C1_default_tmp0 = G12*sourceGI_C1_case0_tmp6;
        double sourceG_C1_default_tmp1 = Gc*sourceG_C1_default_tmp0;
        double sourceG_C1_default_tmp2 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;
        double sourceG_C1_default_tmp3 = IhisC1*sourceG_C1_default_tmp2;
        double sourceG_C1_default_tmp4 = G11*sourceGI_C1_case0_tmp0;
        double sourceG_C1_default_tmp5 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp6;
        double sourceG_C1_default_tmp6 = G12*sourceGI_C1_case0_tmp12;
        double sourceG_C1_default_tmp7 = G11*sourceG_C1_default_tmp0;
        double sourceG_C1_default_tmp8 = Gc*sourceGI_C1_case0_tmp12;
        double sourceG_C1_default_tmp9 = G12*sourceGI_C1_case0_tmp0;
        double sourceG_C1_default_tmp10 = pow(G12, 3.0)*sourceGI_C1_case0_tmp9;
        double sourceG_C1_default_tmp11 = G22*sourceGI_C1_case0_tmp12;
        double sourceG_C1_default_tmp12 = G22*sourceG_C1_default_tmp0;
        multcase_Ihis_C1_N1 = -Gc*IhisC1*sourceGI_C1_case0_tmp0 + Gc*Ihis_p*sourceGI_C1_case0_tmp0 + Gc*Ihis_p*sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9 - Gc*sourceG_C1_default_tmp3 + IhisC1 - IhisC2*sourceG_C1_default_tmp1 - Ihis_s*sourceG_C1_default_tmp1;
        multcase_Ihis_C1_N2 = -G11*Ihis_p*sourceG_C1_default_tmp2 + G11*sourceG_C1_default_tmp3 + IhisC1*sourceG_C1_default_tmp4 - IhisC1*sourceG_C1_default_tmp5 - IhisC2*sourceG_C1_default_tmp6 + IhisC2*sourceG_C1_default_tmp7 - Ihis_p*sourceG_C1_default_tmp4 + Ihis_p*sourceG_C1_default_tmp5 + Ihis_p - Ihis_s*sourceG_C1_default_tmp6 + Ihis_s*sourceG_C1_default_tmp7;
        multcase_Ihis_C1_N3 = -Gc*Ihis_p*sourceG_C1_default_tmp0 + IhisC1*sourceG_C1_default_tmp1 + IhisC2*sourceG_C1_default_tmp8 - IhisC2 + Ihis_s*sourceG_C1_default_tmp8;
        multcase_Ihis_C1_N4 = IhisC1*sourceG_C1_default_tmp10 - IhisC1*sourceG_C1_default_tmp12 + IhisC1*sourceG_C1_default_tmp9 - IhisC2*sourceG_C1_default_tmp11 + IhisC2*sourceG_C1_default_tmp5 - Ihis_p*sourceG_C1_default_tmp10 + Ihis_p*sourceG_C1_default_tmp12 - Ihis_p*sourceG_C1_default_tmp9 - Ihis_s*sourceG_C1_default_tmp11 + Ihis_s*sourceG_C1_default_tmp5 + Ihis_s;
        break;
    }
    }
    /* ************************************************************************
     * CODE-SIDE IHIS VALUE SETUP
     * Update runtime Ihis/history-source values before node-current injection.
     * No internal nodes are eliminated, so assign original Ihis entries directly to Inj.
     * The row order follows the original retained-node order.
     * ************************************************************************ */



    /* Node injection currents follow the retained-node order of the original system. */
    InjN1 = multcase_Ihis_C1_N1;
    InjN2 = -multcase_Ihis_C1_N2;
    InjN3 = multcase_Ihis_C1_N3;
    InjN4 = -multcase_Ihis_C1_N4;

T1_T2:
    /* No internal nodes were eliminated, so there is no Vk recovery step. */