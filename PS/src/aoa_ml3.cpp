#include "aoa_ml3.hpp"

#include <cmath>
#include <limits>

////////////////////////////////////////////////////////////////////////////////
// 三天线、两路来波的联合 ML：一个函数完成响应生成、完整遍历、拟合与评分。
//
// y = alpha1*a(theta1) + alpha2*a(theta2) + n
// n ~ CN(0,sigma^2*I) 时，-log L = 常数 + ||y-A*alpha||^2/sigma^2。
// 每个角度组合解 min_alpha ||y-A*alpha||^2，得到该格点的剖面似然残差。
// 理论闭式解：r=a1^H*a2，b1=a1^H*y，b2=a2^H*y，D=9-|r|^2；
// alpha1=(3*b1-r*b2)/D，alpha2=(3*b2-conj(r)*b1)/D。
// 实际用两列 QR + 再正交化，避免显式求逆以及 D 的相消误差。
////////////////////////////////////////////////////////////////////////////////
AoaStatus aoa_ml_3ant(const AoaInput& input, const AoaConfig& config,
                     AoaWorkspace& workspace, AoaResult& result)
{
    constexpr double pi          = 3.14159265358979323846;
    constexpr double light_speed = 299792458.0;
    const double epsilon         = std::numeric_limits<double>::epsilon();
    const double nan             = std::numeric_limits<double>::quiet_NaN();
    const double infinity        = std::numeric_limits<double>::infinity();
    const double r11             = std::sqrt(3.0);
    const std::size_t count       = config.angle_count;

    result.grid_count   = 0;
    result.input_energy = 0.0;
    result.has_best     = false;
    result.best_i       = 0;
    result.best_j       = 0;

    // 参数与容量先检查。N*N 先判溢出，再计算，禁止静默截断完整结果。
    if (count < 2 || count > std::numeric_limits<std::size_t>::max() / count ||
        !std::isfinite(input.rf_frequency_hz) || input.rf_frequency_hz <= 0.0 ||
        !std::isfinite(config.angle_start_deg) ||
        !std::isfinite(config.angle_step_deg) || config.angle_step_deg <= 0.0 ||
        !std::isfinite(config.rank_tolerance) || config.rank_tolerance <= 0.0 ||
        config.rank_tolerance >= 1.0 ||
        (config.phase_sign != 1 && config.phase_sign != -1)) {
        return AoaStatus::InvalidArgument;
    }
    const double last_angle = config.angle_start_deg + (count - 1) * config.angle_step_deg;
    if (!std::isfinite(last_angle) || config.angle_start_deg < -180.0 || last_angle > 180.0) {
        return AoaStatus::InvalidArgument;
    }
    if (!workspace.steering || workspace.angle_capacity < count ||
        !result.grid || result.grid_capacity < count * count) {
        return AoaStatus::InsufficientCapacity;
    }

    AoaComplex observed[AOA_ANTENNAS];
    double phase_x[AOA_ANTENNAS];
    double phase_y[AOA_ANTENNAS];
    double energy = 0.0;
    const double wave_number = config.phase_sign * (2.0 * pi / light_speed) * input.rf_frequency_hz;

    for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
        if (!std::isfinite(input.fft_real[antenna]) || !std::isfinite(input.fft_imag[antenna]) ||
            !std::isfinite(config.antenna_x_m[antenna]) ||
            !std::isfinite(config.antenna_y_m[antenna])) {
            return AoaStatus::InvalidArgument;
        }
        observed[antenna] = AoaComplex(input.fft_real[antenna], input.fft_imag[antenna]);
        energy += std::norm(observed[antenna]);
        phase_x[antenna] = wave_number * (config.antenna_x_m[antenna] - config.antenna_x_m[0]);
        phase_y[antenna] = wave_number * (config.antenna_y_m[antenna] - config.antenna_y_m[0]);
        if (!std::isfinite(phase_x[antenna]) || !std::isfinite(phase_y[antenna])) {
            return AoaStatus::InvalidArgument;
        }
    }
    if (!std::isfinite(energy)) {
        return AoaStatus::InvalidArgument;
    }
    result.input_energy = energy;
    if (energy <= std::numeric_limits<double>::min()) {
        return AoaStatus::NoSignal;
    }

    // a_m(theta)=exp(j*sign*2*pi*f_RF/c*(dx_m*cos(theta)+dy_m*sin(theta)))。
    // 缓存 N 组三天线响应，避免在 N*N 个格点中重复计算三角函数。
    double previous_angle = -infinity;
    for (std::size_t index = 0; index < count; ++index) {
        const double angle = config.angle_start_deg + index * config.angle_step_deg;
        if (angle <= previous_angle) {
            return AoaStatus::InvalidArgument;  // 浮点步长太小，产生重复坐标。
        }
        previous_angle = angle;
        const double sine   = std::sin(angle * pi / 180.0);
        const double cosine = std::cos(angle * pi / 180.0);
        for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
            const double phase = phase_x[antenna] * cosine + phase_y[antenna] * sine;
            if (!std::isfinite(phase)) {
                return AoaStatus::InvalidArgument;
            }
            workspace.steering[index].antenna[antenna] = AoaComplex(std::cos(phase), std::sin(phase));
        }
    }

    // 完整方阵：不做粗细搜索，不跳过镜像格点，不只保留最大值。
    double best_residual = infinity;
    for (std::size_t first = 0; first < count; ++first) {
        const AoaComplex* a1 = workspace.steering[first].antenna;
        AoaComplex q1[AOA_ANTENNAS];
        AoaComplex c1(0.0, 0.0);
        for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
            q1[antenna] = a1[antenna] / r11;
            c1 += std::conj(q1[antenna]) * observed[antenna];
        }

        for (std::size_t second = 0; second < count; ++second) {
            const AoaComplex* a2 = workspace.steering[second].antenna;
            AoaGridPoint& point  = result.grid[first * count + second];
            point.alpha_real[0] = point.alpha_real[1] = nan;
            point.alpha_imag[0] = point.alpha_imag[1] = nan;
            point.residual      = nan;
            point.independence  = nan;
            point.status        = AoaPointStatus::NumericalFailure;

            // A=[a1,a2]=Q*R；先消除 a2 在 q1 方向上的分量。
            AoaComplex r12(0.0, 0.0);
            for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
                r12 += std::conj(q1[antenna]) * a2[antenna];
            }
            AoaComplex orthogonal[AOA_ANTENNAS];
            AoaComplex correction(0.0, 0.0);
            for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
                orthogonal[antenna] = a2[antenna] - q1[antenna] * r12;
                correction += std::conj(q1[antenna]) * orthogonal[antenna];
            }
            r12 += correction;
            double r22_squared = 0.0;
            for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
                orthogonal[antenna] -= q1[antenna] * correction;
                r22_squared += std::norm(orthogonal[antenna]);
            }
            point.independence = r22_squared / 3.0;

            // 重合响应退化成一路：J=||y-q1*(q1^H*y)||^2。
            // 仅给出该退化模型的残差，不人为拆成两个“确定”的复幅度。
            if (first == second || point.independence <= 64.0 * epsilon * epsilon) {
                point.residual = 0.0;
                for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
                    point.residual += std::norm(observed[antenna] - q1[antenna] * c1);
                }
                point.status = AoaPointStatus::RankDeficient;
                continue;
            }

            const double r22 = std::sqrt(r22_squared);
            AoaComplex c2(0.0, 0.0);
            for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
                c2 += std::conj(orthogonal[antenna] / r22) * observed[antenna];
            }

            // R*alpha=Q^H*y：先解第二路，再回代第一路，无需迭代幅度或相位。
            const AoaComplex alpha2 = c2 / r22;
            const AoaComplex alpha1 = (c1 - r12 * alpha2) / r11;
            double residual = 0.0;
            for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
                const AoaComplex error = observed[antenna] - a1[antenna] * alpha1 - a2[antenna] * alpha2;
                residual += std::norm(error);
            }
            if (!std::isfinite(alpha1.real()) || !std::isfinite(alpha1.imag()) ||
                !std::isfinite(alpha2.real()) || !std::isfinite(alpha2.imag()) ||
                !std::isfinite(residual)) {
                continue;
            }

            point.alpha_real[0] = alpha1.real();
            point.alpha_imag[0] = alpha1.imag();
            point.alpha_real[1] = alpha2.real();
            point.alpha_imag[1] = alpha2.imag();
            point.residual      = residual;
            point.status        = point.independence < config.rank_tolerance
                                ? AoaPointStatus::IllConditioned : AoaPointStatus::Valid;

            if (first < second && point.status == AoaPointStatus::Valid && residual < best_residual) {
                best_residual   = residual;
                result.has_best = true;
                result.best_i   = first;
                result.best_j   = second;
            }
        }
    }
    result.grid_count = count * count;
    return result.has_best ? AoaStatus::Ok : AoaStatus::NoCandidate;
}
