#pragma once
// [六天线测角新增] PS 上的远场确定性复数 ML；不使用 MUSIC，不改原 ML3。
// 对所有严格递增的 K 元角度组合做完整网格搜索（K=1..3）。
// 目标 sum_b ||y_b-A(theta)*alpha_b||²；各频点的复幅度独立最小二乘。
#include "app.hpp"
#include "joint_quality.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <queue>

namespace aoa_app {
// 显示用组合索引；包含退化组合占位，避免跳过退化解后坐标错位。
inline std::size_t joint_combinations(std::size_t n,std::size_t k) {
    if(n<k) return 0;
    return k==1?n:k==2?n*(n-1)/2:n*(n-1)*(n-2)/6;
}
inline std::size_t joint_score_index(std::size_t n,std::size_t k,
                                    const std::array<std::size_t,3>& ix) {
    const auto i=ix[0],j=ix[1],l=ix[2];
    if(k==1) return i;
    if(k==2) return i*(2*n-i-1)/2+j-i-1;
    return joint_combinations(n,3)-joint_combinations(n-i,3)
        +(j-i-1)*(2*n-i-j-2)/2+l-j-1;
}
inline void validate_joint_options(const Options& o) {
    if(o.processing_mode!="position" && o.processing_mode!="angle6" && o.processing_mode!="scan6")
        throw std::invalid_argument("Unknown processing mode");
    if(o.processing_mode=="scan6" && (o.angle_count<2 || o.angle_count>161))
        throw std::invalid_argument("Beam scan requires 2..161 directions");
    if(o.joint_paths<1 || o.joint_paths>3)
        throw std::invalid_argument("Joint path count must be 1..3");
    // 完整三维搜索有明确计算上限；不暗中降低原配置的角度分辨率。
    if(o.processing_mode=="angle6" && (o.angle_count>161 || o.angle_count<std::size_t(o.joint_paths)))
        throw std::invalid_argument("Joint ML requires path_count <= angle_count <= 161");
}

// Exact residual-only per-order retention, before any scan is evaluated.
struct JointPoolEntry {
    double loss=std::numeric_limits<double>::infinity();
    std::array<std::size_t,3> index{};
    bool operator<(const JointPoolEntry& other) const {
        return loss!=other.loss?loss<other.loss:index<other.index;
    }
};
struct JointPool {std::vector<JointPoolEntry> top;};
inline void analyze_joint_search(const Options& o,const FftData& fft,Analysis& result,
                                const std::function<bool()>& cancelled,
                                JointPool* pool=nullptr) {
    std::priority_queue<JointPoolEntry> retained;
    if(pool) pool->top.clear();
    validate_joint_options(o);
    auto& fit=result.joint;
    fit.id=0;fit.channel={0,1,2,3,4,5};
    const std::size_t n=o.angle_count, nb=result.bins.size(), k=std::size_t(o.joint_paths);
    if(!nb) return;
    using C=AoaComplex;
    using Vec=std::array<C,6>;
    const double inf=std::numeric_limits<double>::infinity(), root6=std::sqrt(6.0);
    std::vector<Vec> a(n),y(nb);
    std::vector<C> gram(n*n), h(n*nb);
    std::vector<double> ranked_min(k*n,inf);
    double cx=0,cy=0;
    for(const auto& ant:o.antenna) {cx+=ant.x/6;cy+=ant.y/6;}
    for(std::size_t b=0;b<nb;++b) for(std::size_t ch=0;ch<6;++ch) {
        y[b][ch]=fft.corrected[ch*fft_length+result.bins[b]];
        fit.energy+=std::norm(y[b][ch]);
    }
    if(!std::isfinite(fit.energy)) throw std::runtime_error("Joint energy overflow");
    if(fit.energy<=std::numeric_limits<double>::min()) return;
    if(o.console_enabled || o.save_results) result.joint_score.assign(joint_combinations(n,k),255);
    for(std::size_t i=0;i<n;++i) {
        const double angle=o.angle_start+i*o.angle_step, t=angle*pi/180;
        result.joint_profile_angles.push_back(angle);
        for(std::size_t ch=0;ch<6;++ch)
            a[i][ch]=std::polar(1.0,o.phase_sign*2*pi*o.rf_hz/light_speed*
                ((o.antenna[ch].x-cx)*std::sin(t)+(o.antenna[ch].y-cy)*std::cos(t)));
        for(std::size_t b=0;b<nb;++b) for(std::size_t ch=0;ch<6;++ch)
            h[i*nb+b]+=std::conj(a[i][ch])*y[b][ch];
    }
    for(std::size_t i=0;i<n;++i) for(std::size_t j=i;j<n;++j) {
        C g{};
        for(std::size_t ch=0;ch<6;++ch) g+=std::conj(a[i][ch])*a[j][ch];
        gram[i*n+j]=g;gram[j*n+i]=std::conj(g);
    }
    double best=inf;
    std::array<std::size_t,3> indices{};
    auto accept=[&](double loss,std::array<std::size_t,3> ix) {
        const bool finite_loss=std::isfinite(loss);
        loss=std::max(0.0,loss);
        // 复用本次搜索的残差，仅增加显示输出；原精度的 loss 继续用于全部数学计算。
        if(finite_loss && !result.joint_score.empty())
            result.joint_score[joint_score_index(n,k,ix)]=static_cast<std::uint8_t>(
                std::lround(254*std::clamp(1-loss/fit.energy,0.0,1.0)));
        if(!finite_loss) return;
        if(pool) {
            const JointPoolEntry entry{loss,ix};
            if(retained.size()<30) retained.push(entry);
            else if(entry<retained.top()) {retained.pop();retained.push(entry);}
        }
        if(loss<best) {best=loss;indices=ix;result.selection.path_index=-1;}
        for(std::size_t p=0;p<k;++p)
            ranked_min[p*n+ix[p]]=std::min(ranked_min[p*n+ix[p]],loss);
    };
    // QR 投影的 Gram 递推：只对近退化组合拒绝，不用正规方程求逆。
    // 各新列正交剩余能量/6 <= 1e-12 时不参与搜索。
    std::vector<C> z1(nb),z2(nb);
    for(std::size_t i=0;i<n;++i) {
        if(cancelled && cancelled()) throw std::runtime_error("Joint ML interrupted");
        double e1=fit.energy;
        for(std::size_t b=0;b<nb;++b) {z1[b]=h[i*nb+b]/root6;e1-=std::norm(z1[b]);}
        if(k==1) {accept(e1,{{i,0,0}});continue;}
        for(std::size_t j=i+1;j<n;++j) {
            if(cancelled && cancelled()) throw std::runtime_error("Joint ML interrupted");
            const C r12=gram[i*n+j]/root6;
            const double d2=6-std::norm(r12);
            if(d2<=6e-12) continue;
            const double r22=std::sqrt(d2);
            double e2=e1;
            for(std::size_t b=0;b<nb;++b) {
                z2[b]=(h[j*nb+b]-std::conj(r12)*z1[b])/r22;
                e2-=std::norm(z2[b]);
            }
            if(k==2) {accept(e2,{{i,j,0}});continue;}
            for(std::size_t l=j+1;l<n;++l) {
                const C r13=gram[i*n+l]/root6;
                const C r23=(gram[j*n+l]-std::conj(r12)*r13)/r22;
                const double d3=6-std::norm(r13)-std::norm(r23);
                if(d3<=6e-12) continue;
                double loss=e2;
                for(std::size_t b=0;b<nb;++b)
                    loss-=std::norm(h[l*nb+b]-std::conj(r13)*z1[b]-std::conj(r23)*z2[b])/d3;
                accept(loss,{{i,j,l}});
            }
        }
    }
    if(pool) {
        while(!retained.empty()) {pool->top.push_back(retained.top());retained.pop();}
        std::sort(pool->top.begin(),pool->top.end());
    }
    result.joint_profile_db.assign(n,-80);
    if(!std::isfinite(best)) {fit.core_status=-4;return;}
    // 最优组合用两次正交化重新求系数，并显式重算残差，避免 E-投影能量相消。
    std::array<Vec,3> q{};
    std::array<std::array<C,3>,3> r{};
    for(std::size_t p=0;p<k;++p) {
        q[p]=a[indices[p]];
        for(int pass=0;pass<2;++pass) for(std::size_t j=0;j<p;++j) {
            C dot{};
            for(std::size_t ch=0;ch<6;++ch) dot+=std::conj(q[j][ch])*q[p][ch];
            r[j][p]+=dot;
            for(std::size_t ch=0;ch<6;++ch) q[p][ch]-=q[j][ch]*dot;
        }
        double norm=0;for(auto v:q[p]) norm+=std::norm(v);
        if(norm<=6e-12) {fit.core_status=-4;fit.angle.clear();return;}
        r[p][p]=std::sqrt(norm);
        for(auto& v:q[p]) v/=r[p][p];
        fit.angle.push_back(o.angle_start+indices[p]*o.angle_step);
    }
    fit.has_best=true;fit.core_status=0;
    fit.amplitude_rms.assign(k,0);fit.phase_first_bin_deg.assign(k,0);
    for(std::size_t b=0;b<nb;++b) {
        std::array<C,3> alpha{};
        for(std::size_t p=0;p<k;++p) for(std::size_t ch=0;ch<6;++ch)
            alpha[p]+=std::conj(q[p][ch])*y[b][ch];
        for(int p=int(k)-1;p>=0;--p) {
            for(std::size_t j=p+1;j<k;++j) alpha[p]-=r[p][j]*alpha[j];
            alpha[p]/=r[p][p];
            fit.amplitude_rms[p]+=std::norm(alpha[p]);
            if(b==0) fit.phase_first_bin_deg[p]=std::arg(alpha[p])*180/pi;
        }
        for(std::size_t ch=0;ch<6;++ch) {
            C residual=y[b][ch];
            for(std::size_t p=0;p<k;++p) residual-=a[indices[p]][ch]*alpha[p];
            fit.residual+=std::norm(residual);
        }
    }
    for(auto& amp:fit.amplitude_rms) amp=std::sqrt(amp/nb);
    const double peak=*std::max_element(fit.amplitude_rms.begin(),fit.amplitude_rms.end());
    // 观测与全部候选子空间正交时，零系数不是可显示/跟踪的来波。
    if(!(peak>0)) {
        fit.has_best=false;fit.core_status=-4;fit.angle.clear();
        fit.amplitude_rms.clear();fit.phase_first_bin_deg.clear();return;
    }
    for(auto amp:fit.amplitude_rms) fit.active.push_back(amp>0 && amp>=o.min_path_ratio*peak);
    // condition_F(A)=||A||F ||A+||F，明确采用 Frobenius 定义（正交 K 列时为 K）。
    double inverse_norm=0;
    for(std::size_t col=0;col<k;++col) {
        std::array<C,3> v{};v[col]=1;
        for(int p=int(k)-1;p>=0;--p) {
            for(std::size_t j=p+1;j<k;++j) v[p]-=r[p][j]*v[j];
            v[p]/=r[p][p];inverse_norm+=std::norm(v[p]);
        }
    }
    fit.steering_condition=std::sqrt(6*k*inverse_norm);
    for(std::size_t i=0;i<n;++i) {
        double profile=inf;
        for(std::size_t p=0;p<k;++p) {
            const double loss=ranked_min[p*n+i];
            profile=std::min(profile,loss);
            if(std::abs(double(i)-double(indices[p]))>2 &&
               loss<=best+o.ambiguity_tolerance*fit.energy) fit.ambiguous=true;
        }
        if(std::isfinite(profile))
            result.joint_profile_db[i]=std::clamp(10*std::log10(
                std::max(best/fit.energy,1e-12)/std::max(profile/fit.energy,1e-12)),-80.0,0.0);
    }
}

} // namespace aoa_app
