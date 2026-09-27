#include "ghost_dispatch.h"
#include "GLFunctions.h"
#include "link_dispatch.h"

namespace ghost_dispatch {
namespace {
PROC extension(const char* name) {
    const PROC proc=wglGetProcAddress(name);
    return !proc || proc==reinterpret_cast<PROC>(1) || proc==reinterpret_cast<PROC>(2) ||
        proc==reinterpret_cast<PROC>(3) || proc==reinterpret_cast<PROC>(-1) ? nullptr : proc;
}
}
bool load(ghost_compositor::Dispatch& out) {
    out={};renderer::Identity identity{};snapshot::RestoreBindings bindings{};
    if(!replay_gl::CandidateBindings(&identity,&bindings))return false;
    ghost_compositor::Dispatch d{};
    d.GetIntegerv=reinterpret_cast<decltype(d.GetIntegerv)>(::glGetIntegerv);
    d.GetBooleanv=reinterpret_cast<decltype(d.GetBooleanv)>(::glGetBooleanv);
    d.GetFloatv=reinterpret_cast<decltype(d.GetFloatv)>(::glGetFloatv);
    d.GetDoublev=reinterpret_cast<decltype(d.GetDoublev)>(::glGetDoublev);
    d.GetIntegeri_v=reinterpret_cast<decltype(d.GetIntegeri_v)>(extension("glGetIntegeri_v"));
    d.GetBooleani_v=reinterpret_cast<decltype(d.GetBooleani_v)>(extension("glGetBooleani_v"));
    d.GetFloati_v=reinterpret_cast<decltype(d.GetFloati_v)>(extension("glGetFloati_v"));
    d.GetDoublei_v=reinterpret_cast<decltype(d.GetDoublei_v)>(extension("glGetDoublei_v"));
    d.Enablei=reinterpret_cast<decltype(d.Enablei)>(g_glEnablei);
    d.Disablei=reinterpret_cast<decltype(d.Disablei)>(g_glDisablei);
    d.IsEnabled=reinterpret_cast<decltype(d.IsEnabled)>(::glIsEnabled);
    d.Enable=reinterpret_cast<decltype(d.Enable)>(::glEnable);
    d.Disable=reinterpret_cast<decltype(d.Disable)>(::glDisable);
    d.GenFramebuffers=reinterpret_cast<decltype(d.GenFramebuffers)>(g_glGenFramebuffers);
    d.DeleteFramebuffers=reinterpret_cast<decltype(d.DeleteFramebuffers)>(g_glDeleteFramebuffers);
    d.BindFramebuffer=reinterpret_cast<decltype(d.BindFramebuffer)>(g_glBindFramebuffer);
    d.CheckFramebufferStatus=reinterpret_cast<decltype(d.CheckFramebufferStatus)>(g_glCheckFramebufferStatus);
    d.GetFramebufferAttachmentParameteriv=reinterpret_cast<decltype(d.GetFramebufferAttachmentParameteriv)>(extension("glGetFramebufferAttachmentParameteriv"));
    d.FramebufferTexture2D=reinterpret_cast<decltype(d.FramebufferTexture2D)>(g_glFramebufferTexture2D);
    d.BlitFramebuffer=reinterpret_cast<decltype(d.BlitFramebuffer)>(g_glBlitFramebuffer);
    d.ReadBuffer=reinterpret_cast<decltype(d.ReadBuffer)>(replay_gl::ReadBuffer);
    d.DrawBuffer=reinterpret_cast<decltype(d.DrawBuffer)>(::glDrawBuffer);
    d.GenTextures=reinterpret_cast<decltype(d.GenTextures)>(::glGenTextures);
    d.DeleteTextures=reinterpret_cast<decltype(d.DeleteTextures)>(replay_gl::DeleteTextures);
    d.ActiveTexture=reinterpret_cast<decltype(d.ActiveTexture)>(g_glActiveTexture);
    d.BindTexture=reinterpret_cast<decltype(d.BindTexture)>(replay_gl::BindTexture);
    d.IsTexture=reinterpret_cast<decltype(d.IsTexture)>(::glIsTexture);
    d.GetTexLevelParameteriv=reinterpret_cast<decltype(d.GetTexLevelParameteriv)>(::glGetTexLevelParameteriv);
    d.TexParameteri=reinterpret_cast<decltype(d.TexParameteri)>(::glTexParameteri);
    d.TexImage2D=reinterpret_cast<decltype(d.TexImage2D)>(::glTexImage2D);
    d.TexSubImage2D=reinterpret_cast<decltype(d.TexSubImage2D)>(::glTexSubImage2D);
    d.BindSampler=reinterpret_cast<decltype(d.BindSampler)>(extension("glBindSampler"));
    d.PixelStorei=reinterpret_cast<decltype(d.PixelStorei)>(::glPixelStorei);
    d.GenRenderbuffers=reinterpret_cast<decltype(d.GenRenderbuffers)>(g_glGenRenderbuffers);
    d.DeleteRenderbuffers=reinterpret_cast<decltype(d.DeleteRenderbuffers)>(g_glDeleteRenderbuffers);
    d.BindRenderbuffer=reinterpret_cast<decltype(d.BindRenderbuffer)>(g_glBindRenderbuffer);
    d.RenderbufferStorage=reinterpret_cast<decltype(d.RenderbufferStorage)>(g_glRenderbufferStorage);
    d.FramebufferRenderbuffer=reinterpret_cast<decltype(d.FramebufferRenderbuffer)>(g_glFramebufferRenderbuffer);
    d.GenVertexArrays=reinterpret_cast<decltype(d.GenVertexArrays)>(g_glGenVertexArrays);
    d.DeleteVertexArrays=reinterpret_cast<decltype(d.DeleteVertexArrays)>(g_glDeleteVertexArrays);
    d.BindVertexArray=reinterpret_cast<decltype(d.BindVertexArray)>(g_glBindVertexArray);
    d.GenBuffers=reinterpret_cast<decltype(d.GenBuffers)>(g_glGenBuffers);
    d.DeleteBuffers=reinterpret_cast<decltype(d.DeleteBuffers)>(g_glDeleteBuffers);
    d.BindBuffer=reinterpret_cast<decltype(d.BindBuffer)>(g_glBindBuffer);
    d.BufferData=reinterpret_cast<decltype(d.BufferData)>(g_glBufferData);
    d.BufferSubData=reinterpret_cast<decltype(d.BufferSubData)>(g_glBufferSubData);
    d.EnableVertexAttribArray=reinterpret_cast<decltype(d.EnableVertexAttribArray)>(g_glEnableVertexAttribArray);
    d.VertexAttribPointer=reinterpret_cast<decltype(d.VertexAttribPointer)>(g_glVertexAttribPointer);
    d.CreateShader=reinterpret_cast<decltype(d.CreateShader)>(g_glCreateShader);
    d.ShaderSource=reinterpret_cast<decltype(d.ShaderSource)>(g_glShaderSource);
    d.CompileShader=reinterpret_cast<decltype(d.CompileShader)>(g_glCompileShader);
    d.GetShaderiv=reinterpret_cast<decltype(d.GetShaderiv)>(g_glGetShaderiv);
    d.DeleteShader=reinterpret_cast<decltype(d.DeleteShader)>(g_glDeleteShader);
    d.CreateProgram=reinterpret_cast<decltype(d.CreateProgram)>(g_glCreateProgram);
    d.AttachShader=reinterpret_cast<decltype(d.AttachShader)>(g_glAttachShader);
    d.LinkProgram=reinterpret_cast<decltype(d.LinkProgram)>(g_glLinkProgram);
    d.GetProgramiv=reinterpret_cast<decltype(d.GetProgramiv)>(g_glGetProgramiv);
    d.DeleteProgram=reinterpret_cast<decltype(d.DeleteProgram)>(g_glDeleteProgram);
    d.UseProgram=reinterpret_cast<decltype(d.UseProgram)>(g_glUseProgram);
    d.GetUniformLocation=reinterpret_cast<decltype(d.GetUniformLocation)>(g_glGetUniformLocation);
    d.Uniform1i=reinterpret_cast<decltype(d.Uniform1i)>(g_glUniform1i);
    d.Uniform1f=reinterpret_cast<decltype(d.Uniform1f)>(g_glUniform1f);
    d.Uniform2f=reinterpret_cast<decltype(d.Uniform2f)>(g_glUniform2f);
    d.Uniform3fv=reinterpret_cast<decltype(d.Uniform3fv)>(g_glUniform3fv);
    d.UniformMatrix4fv=reinterpret_cast<decltype(d.UniformMatrix4fv)>(extension("glUniformMatrix4fv"));
    d.Viewport=reinterpret_cast<decltype(d.Viewport)>(::glViewport);
    d.Scissor=reinterpret_cast<decltype(d.Scissor)>(::glScissor);
    d.ViewportIndexedf=reinterpret_cast<decltype(d.ViewportIndexedf)>(extension("glViewportIndexedf"));
    d.ScissorIndexed=reinterpret_cast<decltype(d.ScissorIndexed)>(extension("glScissorIndexed"));
    d.DepthRangeIndexed=reinterpret_cast<decltype(d.DepthRangeIndexed)>(extension("glDepthRangeIndexed"));
    d.DepthRange=reinterpret_cast<decltype(d.DepthRange)>(::glDepthRange);
    d.DepthFunc=reinterpret_cast<decltype(d.DepthFunc)>(::glDepthFunc);
    d.DepthMask=reinterpret_cast<decltype(d.DepthMask)>(::glDepthMask);
    d.ColorMaski=reinterpret_cast<decltype(d.ColorMaski)>(extension("glColorMaski"));
    d.BlendFuncSeparatei=reinterpret_cast<decltype(d.BlendFuncSeparatei)>(extension("glBlendFuncSeparatei"));
    d.BlendEquationSeparatei=reinterpret_cast<decltype(d.BlendEquationSeparatei)>(extension("glBlendEquationSeparatei"));
    d.PolygonMode=reinterpret_cast<decltype(d.PolygonMode)>(::glPolygonMode);
    d.PolygonOffset=reinterpret_cast<decltype(d.PolygonOffset)>(::glPolygonOffset);
    d.StencilFuncSeparate=reinterpret_cast<decltype(d.StencilFuncSeparate)>(extension("glStencilFuncSeparate"));
    d.StencilOpSeparate=reinterpret_cast<decltype(d.StencilOpSeparate)>(extension("glStencilOpSeparate"));
    d.StencilMaskSeparate=reinterpret_cast<decltype(d.StencilMaskSeparate)>(extension("glStencilMaskSeparate"));
    d.ClearBufferfi=reinterpret_cast<decltype(d.ClearBufferfi)>(extension("glClearBufferfi"));
    d.ClearBufferfv=reinterpret_cast<decltype(d.ClearBufferfv)>(extension("glClearBufferfv"));
    d.DrawArrays=reinterpret_cast<decltype(d.DrawArrays)>(::glDrawArrays);
    d.MemoryBarrier=reinterpret_cast<decltype(d.MemoryBarrier)>(g_glMemoryBarrier);
    // Optional (GL 4.4 multi-bind): without them a frame asks the active unit.
    d.BindTextures=reinterpret_cast<decltype(d.BindTextures)>(extension("glBindTextures"));
    d.BindSamplers=reinterpret_cast<decltype(d.BindSamplers)>(extension("glBindSamplers"));
#define GD_REQUIRE(result,name,args) if(!d.name)return false;
    GC_GL_FUNCTIONS(GD_REQUIRE)
#undef GD_REQUIRE
    out=d;return true;
}
}
