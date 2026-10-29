#ifndef AOA_ML3_HPP
#define AOA_ML3_HPP

#include <complex>
#include <cstddef>
#include <cstdint>

// 单次计算固定为三天线、两路来波；六通道分组属于外层数据预处理。
constexpr std::size_t AOA_ANTENNAS = 3;
constexpr std::size_t AOA_PATHS    = 2;
using AoaComplex = std::complex<double>;

enum class AoaStatus : int {
    Ok                   =  0,
    InvalidArgument      = -1,
    InsufficientCapacity = -2,
    NoSignal             = -3,
    NoCandidate          = -4
};

enum class AoaPointStatus : std::uint8_t {
    Valid            = 0,
    RankDeficient    = 1,  // 两列响应重合：保留单路残差，两路系数为 NaN。
    IllConditioned   = 2,  // 仍输出两路拟合结果，但不参与最佳有效点选择。
    NumericalFailure = 3   // 无可靠数值，残差与系数为 NaN。
};

struct AoaInput {
    double fft_real[AOA_ANTENNAS] = {};
    double fft_imag[AOA_ANTENNAS] = {};
    double rf_frequency_hz      = 0.0;  // 实际 RF，不能直接填基带频率。
};

struct AoaConfig {
    double antenna_x_m[AOA_ANTENNAS] = {};
    double antenna_y_m[AOA_ANTENNAS] = {};
    double angle_start_deg          = -80.0;
    double angle_step_deg           = 1.0;
    std::size_t angle_count         = 161;
    int    phase_sign               = 1;
    double rank_tolerance           = 1e-12;
};

struct AoaSteering {
    AoaComplex antenna[AOA_ANTENNAS];
};

struct AoaWorkspace {
    AoaSteering* steering     = nullptr;
    std::size_t angle_capacity = 0;
};

struct AoaGridPoint {
    double alpha_real[AOA_PATHS];
    double alpha_imag[AOA_PATHS];
    double residual;
    double independence;  // 1-|a1^H*a2|^2/9，QR 等价计算，非可辨识性保证。
    AoaPointStatus status;
};

struct AoaResult {
    // 输入：外层申请缓存；输出：核心填写，不申请内存，不打印、不写文件。
    AoaGridPoint* grid       = nullptr;
    std::size_t grid_capacity = 0;
    std::size_t grid_count    = 0;
    double input_energy      = 0.0;
    bool has_best            = false;
    std::size_t best_i        = 0;
    std::size_t best_j        = 0;
};

// 前提：同一窗口、同一频点的已校准观测；水平远场、窄带、等方差白噪声。
// 坐标：角度从 +X 朝 +Y 增加；Y 轴直线阵的 0° 为正侧向。
// 每次以第 0 根天线为参考，alpha 的相位也相对于该参考。
// theta[i] = angle_start_deg + i*angle_step_deg，角域限于 [-180°,180°]。
// grid[i*N+j] 对应 (theta[i],theta[j])，alpha[0/1] 顺序与两坐标严格一致。
// 最佳点只在 i<j 的有效点中选，消除标签互换；仍实际计算、输出全部 N*N 点。
// Ok/NoCandidate 时 grid_count=N*N；其他返回值 grid_count=0，不能读旧结果。
// workspace 与 result 缓存由调用者持有，不得重叠；顺序调用两组时可复用工作区。
AoaStatus aoa_ml_3ant(const AoaInput& input, const AoaConfig& config,
                     AoaWorkspace& workspace, AoaResult& result);

#endif
